-- 220: Tenant-scoped manual memory write capabilities for the web runtime.
-- The legacy functions from migration 144 remain untouched for compatibility.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_runtime_manual_memory_scope(
    p_org_id UUID,
    p_user_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR public.tenant_actor_user_id() IS DISTINCT FROM p_user_id
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'MANUAL_MEMORY_RUNTIME_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.users
         WHERE id = p_user_id AND status::TEXT = 'active'
    ) THEN
        RAISE EXCEPTION 'MANUAL_MEMORY_PRINCIPAL_INACTIVE'
            USING ERRCODE = '42501';
    END IF;
    IF p_org_id IS NOT NULL AND (
        NOT EXISTS (
            SELECT 1 FROM public.organizations
             WHERE id = p_org_id AND status = 'active'
        )
        OR NOT EXISTS (
            SELECT 1 FROM public.org_members
             WHERE org_id = p_org_id
               AND user_id = p_user_id
               AND status = 'active'
        )
    ) THEN
        RAISE EXCEPTION 'MANUAL_MEMORY_ORG_ACCESS_DENIED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION runtime_create_manual_memory(
    p_org_id UUID,
    p_user_id UUID,
    p_content TEXT,
    p_content_hash TEXT,
    p_embedding TEXT,
    p_priority INTEGER DEFAULT 70
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_existing public.memory_atoms%ROWTYPE;
    v_atom public.memory_atoms%ROWTYPE;
    v_count INTEGER;
BEGIN
    PERFORM public._assert_runtime_manual_memory_scope(p_org_id, p_user_id);
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
        p_user_id::TEXT || ':' || COALESCE(p_org_id::TEXT, 'personal'), 0
    ));
    SELECT * INTO v_existing
      FROM public.memory_atoms
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

    SELECT COUNT(*) INTO v_count
      FROM public.memory_atoms
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted;
    IF v_count >= 100 THEN
        RETURN jsonb_build_object('outcome', 'limit_reached');
    END IF;

    INSERT INTO public.memory_atoms(
        org_id, user_id, content, type, priority, scene_name,
        source_message_ids, embedding, content_tsv, metadata,
        status, explicitness, confirmed_by_user, content_hash,
        source_kind, created_at, updated_at
    ) VALUES (
        p_org_id, p_user_id, BTRIM(p_content), 'persona', p_priority, '',
        '{}'::UUID[], p_embedding::vector,
        to_tsvector('simple', BTRIM(p_content)),
        jsonb_build_object('kind', 'reusable_context', 'source', 'manual'),
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

CREATE OR REPLACE FUNCTION runtime_update_manual_memory(
    p_org_id UUID,
    p_user_id UUID,
    p_memory_id UUID,
    p_content TEXT,
    p_content_hash TEXT,
    p_embedding TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_target public.memory_atoms%ROWTYPE;
    v_duplicate_id UUID;
BEGIN
    PERFORM public._assert_runtime_manual_memory_scope(p_org_id, p_user_id);
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
        p_user_id::TEXT || ':' || COALESCE(p_org_id::TEXT, 'personal'), 0
    ));
    SELECT * INTO v_target
      FROM public.memory_atoms
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

    SELECT id INTO v_duplicate_id
      FROM public.memory_atoms
     WHERE id <> p_memory_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND content_hash = p_content_hash
       AND status = 'active'
       AND NOT is_deleted
     LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object('outcome', 'duplicate', 'id', v_duplicate_id);
    END IF;

    UPDATE public.memory_atoms
       SET content = BTRIM(p_content),
           embedding = p_embedding::vector,
           content_tsv = to_tsvector('simple', BTRIM(p_content)),
           content_hash = p_content_hash,
           updated_at = NOW()
     WHERE id = p_memory_id;
    RETURN jsonb_build_object(
        'outcome', 'updated', 'id', p_memory_id, 'updated_at', NOW()
    );
END;
$$;

CREATE OR REPLACE FUNCTION runtime_delete_memory_atom(
    p_org_id UUID,
    p_user_id UUID,
    p_memory_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted_id UUID;
BEGIN
    PERFORM public._assert_runtime_manual_memory_scope(p_org_id, p_user_id);
    UPDATE public.memory_atoms
       SET status = 'deleted', is_deleted = TRUE, updated_at = NOW()
     WHERE id = p_memory_id
       AND user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted
    RETURNING id INTO v_deleted_id;
    RETURN jsonb_build_object(
        'outcome',
        CASE WHEN v_deleted_id IS NULL THEN 'not_found' ELSE 'deleted' END,
        'id', v_deleted_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION runtime_clear_memory_atoms(
    p_org_id UUID,
    p_user_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    PERFORM public._assert_runtime_manual_memory_scope(p_org_id, p_user_id);
    UPDATE public.memory_atoms
       SET status = 'deleted', is_deleted = TRUE, updated_at = NOW()
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'active'
       AND NOT is_deleted;
    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;
    RETURN jsonb_build_object(
        'outcome', 'cleared', 'deleted_count', v_deleted_count
    );
END;
$$;

REVOKE ALL ON FUNCTION
    _assert_runtime_manual_memory_scope(UUID, UUID),
    runtime_create_manual_memory(UUID, UUID, TEXT, TEXT, TEXT, INTEGER),
    runtime_update_manual_memory(UUID, UUID, UUID, TEXT, TEXT, TEXT),
    runtime_delete_memory_atom(UUID, UUID, UUID),
    runtime_clear_memory_atoms(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION
    runtime_create_manual_memory(UUID, UUID, TEXT, TEXT, TEXT, INTEGER),
    runtime_update_manual_memory(UUID, UUID, UUID, TEXT, TEXT, TEXT),
    runtime_delete_memory_atom(UUID, UUID, UUID),
    runtime_clear_memory_atoms(UUID, UUID)
TO everydayai_runtime;

RESET ROLE;
