-- 144: 通用手动 Curated Memory 与个人 scope 原子协议。
-- 依赖 140_generic_memory_session_runtime.sql。

ALTER TABLE memory_atoms
    ALTER COLUMN org_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL
        DEFAULT 'conversation';

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_atoms_source_kind_check'
          AND conrelid = 'memory_atoms'::regclass
    ) THEN
        ALTER TABLE memory_atoms
            ADD CONSTRAINT memory_atoms_source_kind_check
            CHECK (source_kind IN ('conversation', 'manual', 'skill'));
    END IF;
END
$constraints$;

CREATE INDEX IF NOT EXISTS idx_memory_atoms_personal_active
    ON memory_atoms(user_id, updated_at DESC)
    WHERE org_id IS NULL AND status = 'active' AND NOT is_deleted;

CREATE INDEX IF NOT EXISTS idx_memory_atoms_org_active
    ON memory_atoms(org_id, user_id, updated_at DESC)
    WHERE org_id IS NOT NULL AND status = 'active' AND NOT is_deleted;

CREATE OR REPLACE FUNCTION create_manual_memory(
    p_org_id UUID,
    p_user_id UUID,
    p_content TEXT,
    p_content_hash TEXT,
    p_embedding TEXT,
    p_priority INTEGER DEFAULT 70
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_existing memory_atoms%ROWTYPE;
    v_atom memory_atoms%ROWTYPE;
    v_count INTEGER;
BEGIN
    IF p_user_id IS NULL
       OR NULLIF(BTRIM(p_content), '') IS NULL
       OR LENGTH(p_content) > 500
       OR NULLIF(BTRIM(p_content_hash), '') IS NULL
       OR NULLIF(BTRIM(p_embedding), '') IS NULL
       OR p_priority NOT BETWEEN 0 AND 100 THEN
        RAISE EXCEPTION 'MANUAL_MEMORY_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_user_id::TEXT || ':' || COALESCE(p_org_id::TEXT, 'personal'),
        0
    ));

    SELECT *
      INTO v_existing
      FROM memory_atoms
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND content_hash = p_content_hash
       AND status = 'active'
       AND NOT is_deleted
     LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'existing',
            'id', v_existing.id,
            'created_at', v_existing.created_at,
            'updated_at', v_existing.updated_at
        );
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM memory_atoms
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted;
    IF v_count >= 100 THEN
        RETURN jsonb_build_object('outcome', 'limit_reached');
    END IF;

    INSERT INTO memory_atoms (
        org_id, user_id, content, type, priority, scene_name,
        source_message_ids, embedding, content_tsv, metadata,
        status, explicitness, confirmed_by_user, content_hash,
        source_kind, created_at, updated_at
    ) VALUES (
        p_org_id, p_user_id, BTRIM(p_content), 'persona', p_priority, '',
        '{}'::UUID[], p_embedding::vector,
        to_tsvector('simple', BTRIM(p_content)),
        jsonb_build_object(
            'kind', 'reusable_context',
            'source', 'manual'
        ),
        'active', 'confirmed', TRUE, p_content_hash,
        'manual', NOW(), NOW()
    )
    RETURNING * INTO v_atom;

    RETURN jsonb_build_object(
        'outcome', 'created',
        'id', v_atom.id,
        'created_at', v_atom.created_at,
        'updated_at', v_atom.updated_at
    );
END;
$$;

CREATE OR REPLACE FUNCTION update_manual_memory(
    p_org_id UUID,
    p_user_id UUID,
    p_memory_id UUID,
    p_content TEXT,
    p_content_hash TEXT,
    p_embedding TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_target memory_atoms%ROWTYPE;
    v_duplicate_id UUID;
BEGIN
    IF p_user_id IS NULL
       OR p_memory_id IS NULL
       OR NULLIF(BTRIM(p_content), '') IS NULL
       OR LENGTH(p_content) > 500
       OR NULLIF(BTRIM(p_content_hash), '') IS NULL
       OR NULLIF(BTRIM(p_embedding), '') IS NULL THEN
        RAISE EXCEPTION 'MANUAL_MEMORY_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_user_id::TEXT || ':' || COALESCE(p_org_id::TEXT, 'personal'),
        0
    ));

    SELECT *
      INTO v_target
      FROM memory_atoms
     WHERE id = p_memory_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND source_kind = 'manual'
       AND status = 'active'
       AND NOT is_deleted
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    SELECT id
      INTO v_duplicate_id
      FROM memory_atoms
     WHERE id <> p_memory_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND content_hash = p_content_hash
       AND status = 'active'
       AND NOT is_deleted
     LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'duplicate',
            'id', v_duplicate_id
        );
    END IF;

    UPDATE memory_atoms
       SET content = BTRIM(p_content),
           embedding = p_embedding::vector,
           content_tsv = to_tsvector('simple', BTRIM(p_content)),
           content_hash = p_content_hash,
           updated_at = NOW()
     WHERE id = p_memory_id;

    RETURN jsonb_build_object(
        'outcome', 'updated',
        'id', p_memory_id,
        'updated_at', NOW()
    );
END;
$$;

CREATE OR REPLACE FUNCTION delete_memory_atom(
    p_org_id UUID,
    p_user_id UUID,
    p_memory_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_deleted_id UUID;
BEGIN
    UPDATE memory_atoms
       SET status = 'deleted',
           is_deleted = TRUE,
           updated_at = NOW()
     WHERE id = p_memory_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted
    RETURNING id INTO v_deleted_id;

    RETURN jsonb_build_object(
        'outcome', CASE
            WHEN v_deleted_id IS NULL THEN 'not_found'
            ELSE 'deleted'
        END,
        'id', v_deleted_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION clear_memory_atoms(
    p_org_id UUID,
    p_user_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    UPDATE memory_atoms
       SET status = 'deleted',
           is_deleted = TRUE,
           updated_at = NOW()
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted;
    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;

    RETURN jsonb_build_object(
        'outcome', 'cleared',
        'deleted_count', v_deleted_count
    );
END;
$$;

DO $grant$
BEGIN
    REVOKE ALL ON FUNCTION create_manual_memory(
        UUID, UUID, TEXT, TEXT, TEXT, INTEGER
    ) FROM PUBLIC;
    REVOKE ALL ON FUNCTION update_manual_memory(
        UUID, UUID, UUID, TEXT, TEXT, TEXT
    ) FROM PUBLIC;
    REVOKE ALL ON FUNCTION delete_memory_atom(
        UUID, UUID, UUID
    ) FROM PUBLIC;
    REVOKE ALL ON FUNCTION clear_memory_atoms(
        UUID, UUID
    ) FROM PUBLIC;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION create_manual_memory(
            UUID, UUID, TEXT, TEXT, TEXT, INTEGER
        ) TO service_role;
        GRANT EXECUTE ON FUNCTION update_manual_memory(
            UUID, UUID, UUID, TEXT, TEXT, TEXT
        ) TO service_role;
        GRANT EXECUTE ON FUNCTION delete_memory_atom(
            UUID, UUID, UUID
        ) TO service_role;
        GRANT EXECUTE ON FUNCTION clear_memory_atoms(
            UUID, UUID
        ) TO service_role;
    END IF;
END
$grant$;
