-- 143: Consolidation Run、Curated Atom 与 Session 消费的原子提交。
-- 依赖 142_memory_consolidation_runtime.sql。

CREATE OR REPLACE FUNCTION commit_memory_consolidation(
    p_org_id UUID,
    p_user_id UUID,
    p_source_log_ids UUID[],
    p_source_hash TEXT,
    p_operations JSONB,
    p_model TEXT,
    p_prompt_version TEXT,
    p_receipt JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_run_id UUID := gen_random_uuid();
    v_existing memory_consolidation_runs%ROWTYPE;
    v_operation JSONB;
    v_relation TEXT;
    v_atom_id UUID;
    v_related_ids UUID[];
    v_source_ids UUID[];
    v_ready_count INTEGER;
    v_related_count INTEGER;
    v_input_count INTEGER;
    v_promoted_count INTEGER := 0;
BEGIN
    IF p_source_log_ids IS NULL
       OR cardinality(p_source_log_ids) NOT BETWEEN 3 AND 25
       OR cardinality(p_source_log_ids)
            <> cardinality(ARRAY(SELECT DISTINCT unnest(p_source_log_ids)))
       OR NULLIF(BTRIM(p_source_hash), '') IS NULL
       OR p_operations IS NULL
       OR jsonb_typeof(p_operations) <> 'array'
       OR pg_column_size(p_operations) > 4194304
       OR NULLIF(BTRIM(p_model), '') IS NULL
       OR NULLIF(BTRIM(p_prompt_version), '') IS NULL
       OR p_receipt IS NULL
       OR jsonb_typeof(p_receipt) <> 'object'
       OR pg_column_size(p_receipt) > 262144 THEN
        RAISE EXCEPTION 'MEMORY_CONSOLIDATION_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT *
      INTO v_existing
      FROM memory_consolidation_runs
     WHERE user_id = p_user_id AND source_hash = p_source_hash;
    IF FOUND THEN
        RETURN jsonb_build_object(
            'outcome', 'already_committed',
            'run_id', v_existing.id,
            'promoted_count', v_existing.output_count
        );
    END IF;

    PERFORM id
      FROM memory_session_logs
     WHERE id = ANY(p_source_log_ids)
     ORDER BY id
     FOR UPDATE;

    SELECT COUNT(*)
      INTO v_ready_count
      FROM memory_session_logs
     WHERE id = ANY(p_source_log_ids)
       AND user_id = p_user_id
       AND status = 'ready';
    IF v_ready_count <> cardinality(p_source_log_ids) THEN
        RETURN jsonb_build_object('outcome', 'stale_sources');
    END IF;

    v_input_count := jsonb_array_length(p_operations);
    FOR v_operation IN SELECT value FROM jsonb_array_elements(p_operations)
    LOOP
        v_relation := v_operation->>'relation';
        IF jsonb_typeof(v_operation) <> 'object'
           OR v_relation NOT IN (
               'novel', 'duplicate', 'supersedes', 'conflicts'
           )
           OR COALESCE(v_operation->>'legacy_type', '') NOT IN (
               'persona', 'episodic', 'instruction'
           )
           OR NULLIF(BTRIM(v_operation->>'content'), '') IS NULL
           OR LENGTH(v_operation->>'content') > 1000
           OR NULLIF(BTRIM(v_operation->>'content_hash'), '') IS NULL
           OR COALESCE((v_operation->>'priority')::INTEGER, -1)
                NOT BETWEEN 0 AND 100
           OR COALESCE(v_operation->>'explicitness', '') NOT IN (
               'explicit', 'confirmed'
           )
           OR jsonb_typeof(v_operation->'related_memory_ids') <> 'array'
           OR jsonb_typeof(v_operation->'source_message_ids') <> 'array'
           OR jsonb_array_length(v_operation->'source_message_ids') = 0
           OR jsonb_typeof(v_operation->'metadata') <> 'object'
           OR (v_relation <> 'duplicate'
               AND jsonb_typeof(v_operation->'embedding') <> 'array')
           OR (v_relation = 'novel'
               AND jsonb_array_length(v_operation->'related_memory_ids') <> 0)
           OR (v_relation <> 'novel'
               AND jsonb_array_length(v_operation->'related_memory_ids') = 0)
           OR NOT ((v_operation->>'source_session_log_id')::UUID
                = ANY(p_source_log_ids)) THEN
            RAISE EXCEPTION 'MEMORY_CONSOLIDATION_OPERATION_INVALID'
                USING ERRCODE = '22023';
        END IF;

        SELECT COALESCE(array_agg(value::UUID), '{}'::UUID[])
          INTO v_related_ids
          FROM jsonb_array_elements_text(
              v_operation->'related_memory_ids'
          );
        SELECT COALESCE(array_agg(value::UUID), '{}'::UUID[])
          INTO v_source_ids
          FROM jsonb_array_elements_text(
              v_operation->'source_message_ids'
          );

        IF v_relation <> 'novel' THEN
            PERFORM id
              FROM memory_atoms
             WHERE id = ANY(v_related_ids)
               AND org_id = p_org_id
               AND user_id = p_user_id
               AND status = 'active'
               AND NOT is_deleted
             ORDER BY id
             FOR UPDATE;
            SELECT COUNT(*)
              INTO v_related_count
              FROM memory_atoms
             WHERE id = ANY(v_related_ids)
               AND org_id = p_org_id
               AND user_id = p_user_id
               AND status = 'active'
               AND NOT is_deleted;
            IF v_related_count <> cardinality(v_related_ids) THEN
                RETURN jsonb_build_object('outcome', 'stale_curated');
            END IF;
        END IF;

        IF v_relation = 'duplicate' THEN
            CONTINUE;
        END IF;

        v_atom_id := gen_random_uuid();
        INSERT INTO memory_atoms (
            id, org_id, user_id, content, type, priority, scene_name,
            source_message_ids, session_id, embedding, content_tsv, metadata,
            status, source_session_log_id, explicitness,
            valid_from, valid_until, content_hash,
            created_at, updated_at
        ) VALUES (
            v_atom_id,
            p_org_id,
            p_user_id,
            v_operation->>'content',
            v_operation->>'legacy_type',
            (v_operation->>'priority')::INTEGER,
            '',
            v_source_ids,
            NULL,
            (v_operation->'embedding')::TEXT::vector,
            to_tsvector('simple', v_operation->>'content'),
            jsonb_set(
                v_operation->'metadata',
                '{consolidation_run_id}',
                to_jsonb(v_run_id::TEXT),
                TRUE
            ),
            CASE
                WHEN v_relation = 'conflicts' THEN 'conflict'
                ELSE 'active'
            END,
            (v_operation->>'source_session_log_id')::UUID,
            v_operation->>'explicitness',
            NULLIF(v_operation->>'valid_from', '')::TIMESTAMPTZ,
            NULLIF(v_operation->>'valid_until', '')::TIMESTAMPTZ,
            v_operation->>'content_hash',
            NOW(),
            NOW()
        );
        v_promoted_count := v_promoted_count + 1;

        IF v_relation = 'supersedes' THEN
            UPDATE memory_atoms
               SET status = 'superseded',
                   superseded_by = v_atom_id,
                   updated_at = NOW()
             WHERE id = ANY(v_related_ids);
        ELSIF v_relation = 'conflicts' THEN
            UPDATE memory_atoms
               SET status = 'conflict',
                   updated_at = NOW()
             WHERE id = ANY(v_related_ids);
        END IF;
    END LOOP;

    INSERT INTO memory_consolidation_runs (
        id, user_id, source_log_ids, source_hash,
        input_count, output_count, status,
        model, prompt_version, receipt, completed_at
    ) VALUES (
        v_run_id, p_user_id, p_source_log_ids, p_source_hash,
        v_input_count, v_promoted_count, 'completed',
        p_model, p_prompt_version, p_receipt, NOW()
    );

    UPDATE memory_session_logs
       SET status = 'consolidated',
           consolidation_run_id = v_run_id,
           consolidated_at = NOW()
     WHERE id = ANY(p_source_log_ids)
       AND user_id = p_user_id
       AND status = 'ready';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'MEMORY_CONSOLIDATION_SOURCE_UPDATE_FAILED'
            USING ERRCODE = '40001';
    END IF;

    RETURN jsonb_build_object(
        'outcome', 'committed',
        'run_id', v_run_id,
        'promoted_count', v_promoted_count
    );
END;
$$;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION commit_memory_consolidation(
            UUID, UUID, UUID[], TEXT, JSONB, TEXT, TEXT, JSONB
        ) TO service_role;
    END IF;
END
$grant$;
