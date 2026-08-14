-- 228.08d: atomically adopt one prepared Web image batch into Runtime.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION submit_agent_runtime_media_image_batch_v1(
    p_conversation_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_scope_kind TEXT,
    p_scope_id TEXT,
    p_created_by_user_id UUID,
    p_agent_definition_id TEXT,
    p_agent_definition_revision TEXT,
    p_input_message_id UUID,
    p_output_message_id UUID,
    p_turn_id UUID,
    p_batch_id TEXT,
    p_model_id TEXT,
    p_model_provider TEXT,
    p_model_revision TEXT,
    p_catalog_revision TEXT,
    p_policy_revision TEXT,
    p_items JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    item JSONB;
    item_count INTEGER;
    locked_count INTEGER := 0;
    locked_task_id UUID;
    runtime_result JSONB;
    rejected_result JSONB;
    results JSONB := '[]'::JSONB;
    created_count INTEGER := 0;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 10
       OR NULLIF(btrim(COALESCE(p_batch_id, '')), '') IS NULL
       OR length(btrim(p_batch_id)) > 200
       OR p_input_message_id IS NULL
       OR p_output_message_id IS NULL
       OR NULLIF(btrim(COALESCE(p_model_id, '')), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_INVALID'
            USING ERRCODE = '22023';
    END IF;
    item_count := jsonb_array_length(p_items);
    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_items) input(item)
         WHERE jsonb_typeof(input.item) IS DISTINCT FROM 'object'
            OR NULLIF(btrim(input.item->>'task_id'), '') IS NULL
            OR NULLIF(btrim(input.item->>'idempotency_key'), '') IS NULL
            OR length(btrim(input.item->>'idempotency_key')) > 200
            OR jsonb_typeof(input.item->'arguments') IS DISTINCT FROM 'object'
            OR input.item->'arguments'->>'model' IS DISTINCT FROM p_model_id
    ) OR (
        SELECT count(DISTINCT input.item->>'task_id')
          FROM jsonb_array_elements(p_items) input(item)
    ) <> item_count OR (
        SELECT count(DISTINCT input.item->>'idempotency_key')
          FROM jsonb_array_elements(p_items) input(item)
    ) <> item_count THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_INVALID'
            USING ERRCODE = '22023';
    END IF;

    -- A stable lock order makes concurrent retries serialize without deadlocks.
    FOR locked_task_id IN
        SELECT task.id
          FROM tasks task
          JOIN jsonb_array_elements(p_items) input(item)
            ON task.id = (input.item->>'task_id')::UUID
         WHERE task.conversation_id = p_conversation_id
           AND task.user_id = p_user_id
           AND task.org_id IS NOT DISTINCT FROM p_org_id
           AND task.type::TEXT = 'image'
           AND task.input_message_id = p_input_message_id
           AND task.assistant_message_id = p_output_message_id
           AND task.batch_id IS NOT DISTINCT FROM p_batch_id
           AND task.model_id IS NOT DISTINCT FROM p_model_id
         ORDER BY task.id
         FOR UPDATE OF task
    LOOP
        locked_count := locked_count + 1;
    END LOOP;
    IF locked_count <> item_count THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    -- The nested block is a PostgreSQL subtransaction. A conclusive rejection
    -- rolls back every Action, binding and credit lock created earlier in it.
    BEGIN
        FOR item IN
            SELECT input.item
              FROM jsonb_array_elements(p_items) WITH ORDINALITY input(item, ordinal)
             ORDER BY input.ordinal
        LOOP
            runtime_result := submit_agent_runtime_media_action_v1(
                p_conversation_id,
                p_org_id,
                p_user_id,
                p_scope_kind,
                p_scope_id,
                p_created_by_user_id,
                p_agent_definition_id,
                p_agent_definition_revision,
                (item->>'task_id')::UUID,
                p_input_message_id,
                p_output_message_id,
                p_turn_id,
                'generate_image',
                item->'arguments',
                p_model_id,
                p_model_provider,
                p_model_revision,
                p_catalog_revision,
                p_policy_revision,
                item->>'idempotency_key'
            );
            IF COALESCE((runtime_result->>'runtime_owned')::BOOLEAN, FALSE) IS NOT TRUE
               OR runtime_result->>'outcome' NOT IN ('created', 'already_exists') THEN
                rejected_result := runtime_result;
                RAISE SQLSTATE 'PAB01'
                    USING MESSAGE = 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_NOT_OWNED';
            END IF;
            IF runtime_result->>'outcome' = 'created' THEN
                created_count := created_count + 1;
            END IF;
            results := results || jsonb_build_array(
                runtime_result || jsonb_build_object('task_id', item->>'task_id')
            );
        END LOOP;
    EXCEPTION WHEN SQLSTATE 'PAB01' THEN
        NULL;
    END;

    IF rejected_result IS NOT NULL THEN
        RETURN jsonb_build_object(
            'outcome', rejected_result->>'outcome',
            'runtime_owned', FALSE,
            'readiness_revision', rejected_result->'readiness_revision',
            'results', '[]'::JSONB
        );
    END IF;
    RETURN jsonb_build_object(
        'outcome', CASE WHEN created_count > 0 THEN 'created' ELSE 'already_exists' END,
        'runtime_owned', TRUE,
        'results', results
    );
END;
$$;

REVOKE ALL ON FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID,
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
) FROM PUBLIC, everydayai_worker, everydayai_sync, everydayai,
       everydayai_agent_runtime_worker, everydayai_projection_worker,
       everydayai_authorization_worker, everydayai_sandbox_worker,
       everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID,
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
) TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
