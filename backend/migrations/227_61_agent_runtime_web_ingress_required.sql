-- 227.61: Web ingress is Runtime-required; never restore the legacy Actor owner.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION runtime_submit_ingress_v6_required(
 p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,p_scope_id TEXT,
 p_created_by_user_id UUID,p_agent_definition_id TEXT,p_agent_definition_revision TEXT,
 p_agent_definition_hash TEXT,p_command_type TEXT,p_idempotency_key TEXT,p_channel TEXT,
 p_through_message_id UUID,p_base_context_revision TEXT,p_effective_toolset_revision TEXT,
 p_effective_toolset_hash TEXT,p_config_snapshot JSONB,p_capability_snapshot JSONB,
 p_release_revision TEXT,p_payload JSONB,p_task_id UUID,p_client_task_id TEXT,
 p_input_message_id UUID,p_output_message_id UUID,p_turn_id UUID,p_request_id TEXT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    result JSONB;
    task tasks%ROWTYPE;
    owner_result JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF p_request_id IS DISTINCT FROM p_idempotency_key THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_REQUEST_ID_MISMATCH' USING ERRCODE = '42501';
    END IF;

    result := runtime_submit_ingress_v5(
        p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
        p_created_by_user_id,p_agent_definition_id,p_agent_definition_revision,
        p_agent_definition_hash,p_command_type,p_idempotency_key,p_channel,
        p_through_message_id,p_base_context_revision,p_effective_toolset_revision,
        p_effective_toolset_hash,p_config_snapshot,p_capability_snapshot,
        p_release_revision,p_payload);

    IF result->>'outcome' NOT IN ('created','already_exists') THEN
        SELECT * INTO task FROM tasks WHERE id = p_task_id FOR UPDATE;
        IF task.id IS NULL THEN
            RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_MISSING' USING ERRCODE = '42501';
        END IF;
        IF NOT (task.delivery_context @> '{"actor":true}'::JSONB)
           OR COALESCE((task.delivery_context->>'runtime')::BOOLEAN, FALSE) THEN
            RAISE EXCEPTION 'RUNTIME_REQUIRED_TASK_OWNER_STATE_MISMATCH'
                USING ERRCODE = '55000';
        END IF;
        UPDATE tasks
           SET delivery_context = task.delivery_context || jsonb_build_object(
               'actor', FALSE, 'runtime', TRUE,
               'runtime_rejected', TRUE,
               'runtime_rejection_code', COALESCE(result->>'outcome', 'unknown'))
         WHERE id = p_task_id;
        RETURN result || jsonb_build_object(
            'outcome', 'runtime_required_unavailable',
            'runtime_owned', FALSE);
    END IF;

    owner_result := mark_prepared_task_runtime_owned(
        p_task_id,p_conversation_id,p_user_id,p_org_id,p_input_message_id,
        p_output_message_id,p_turn_id,p_through_message_id,p_base_context_revision,
        p_idempotency_key,p_client_task_id,(result->>'session_id')::UUID,
        (result->>'entity_id')::UUID);
    RETURN result || owner_result || jsonb_build_object('runtime_owned', TRUE);
END $$;

REVOKE ALL ON FUNCTION runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
FROM PUBLIC, everydayai_worker, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v6_required(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
TO everydayai_runtime;

RESET ROLE;
