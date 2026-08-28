-- 141: Session Memory Flush revision cursor 与原子 CAS 提交。
-- 依赖 140_generic_memory_session_runtime.sql。

ALTER TABLE memory_pipeline_state
    ADD COLUMN IF NOT EXISTS l1_cursor_revision BIGINT NOT NULL DEFAULT 0;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_pipeline_state_cursor_revision_check'
          AND conrelid = 'memory_pipeline_state'::regclass
    ) THEN
        ALTER TABLE memory_pipeline_state
            ADD CONSTRAINT memory_pipeline_state_cursor_revision_check
            CHECK (l1_cursor_revision >= 0);
    END IF;
END
$constraints$;

CREATE OR REPLACE FUNCTION commit_memory_session_flush(
    p_org_id UUID,
    p_user_id UUID,
    p_conversation_id UUID,
    p_expected_revision BIGINT,
    p_through_revision BIGINT,
    p_trigger TEXT,
    p_content JSONB,
    p_source_refs JSONB,
    p_content_hash TEXT,
    p_model TEXT,
    p_prompt_version TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_cursor BIGINT;
    v_log_id UUID;
BEGIN
    IF p_expected_revision < 0
       OR p_through_revision <= p_expected_revision
       OR jsonb_typeof(p_content) <> 'object'
       OR jsonb_typeof(p_source_refs) <> 'array'
       OR jsonb_array_length(p_source_refs) > 100
       OR pg_column_size(p_content) > 262144
       OR pg_column_size(p_source_refs) > 262144
       OR NULLIF(BTRIM(p_trigger), '') IS NULL
       OR NULLIF(BTRIM(p_content_hash), '') IS NULL
       OR NULLIF(BTRIM(p_model), '') IS NULL
       OR NULLIF(BTRIM(p_prompt_version), '') IS NULL THEN
        RAISE EXCEPTION 'MEMORY_FLUSH_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT l1_cursor_revision
      INTO v_cursor
      FROM memory_pipeline_state
     WHERE org_id = p_org_id
       AND user_id = p_user_id
       AND session_id = p_conversation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'MEMORY_FLUSH_STATE_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;

    IF v_cursor >= p_through_revision THEN
        SELECT id
          INTO v_log_id
          FROM memory_session_logs
         WHERE conversation_id = p_conversation_id
           AND from_revision = p_expected_revision
           AND through_revision = p_through_revision
           AND prompt_version = p_prompt_version;
        RETURN jsonb_build_object(
            'outcome', 'already_committed',
            'cursor_revision', v_cursor,
            'session_log_id', v_log_id
        );
    END IF;

    IF v_cursor <> p_expected_revision THEN
        RETURN jsonb_build_object(
            'outcome', 'stale',
            'cursor_revision', v_cursor
        );
    END IF;

    INSERT INTO memory_session_logs (
        user_id,
        conversation_id,
        from_revision,
        through_revision,
        trigger,
        content,
        source_refs,
        content_hash,
        status,
        model,
        prompt_version
    ) VALUES (
        p_user_id,
        p_conversation_id,
        p_expected_revision,
        p_through_revision,
        p_trigger,
        p_content,
        p_source_refs,
        p_content_hash,
        'ready',
        p_model,
        p_prompt_version
    )
    RETURNING id INTO v_log_id;

    UPDATE memory_pipeline_state
       SET l1_cursor_revision = p_through_revision,
           last_l1_at = NOW(),
           updated_at = NOW()
     WHERE org_id = p_org_id
       AND user_id = p_user_id
       AND session_id = p_conversation_id
       AND l1_cursor_revision = p_expected_revision;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'MEMORY_FLUSH_CURSOR_UPDATE_FAILED'
            USING ERRCODE = '40001';
    END IF;

    RETURN jsonb_build_object(
        'outcome', 'committed',
        'cursor_revision', p_through_revision,
        'session_log_id', v_log_id
    );
END;
$$;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION commit_memory_session_flush(
            UUID, UUID, UUID, BIGINT, BIGINT, TEXT, JSONB, JSONB,
            TEXT, TEXT, TEXT
        ) TO service_role;
    END IF;
END
$grant$;
