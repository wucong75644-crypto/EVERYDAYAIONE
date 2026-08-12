SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION restore_prepared_task_to_legacy_actor(
    p_task_id UUID, p_conversation_id UUID, p_user_id UUID, p_org_id UUID,
    p_input_message_id UUID, p_output_message_id UUID, p_turn_id UUID,
    p_through_message_id UUID, p_base_context_revision TEXT,
    p_idempotency_key TEXT, p_client_task_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_task tasks%ROWTYPE; v_context JSONB;
BEGIN
    v_task := _agent_runtime_validate_task_binding(
        p_task_id, p_conversation_id, p_user_id, p_org_id,
        p_input_message_id, p_output_message_id, p_turn_id,
        p_through_message_id, p_base_context_revision,
        p_idempotency_key, p_client_task_id);
    IF v_task.delivery_context @> '{"actor":true,"runtime":false}'::JSONB THEN
        RETURN jsonb_build_object('outcome', 'already_actor_owned');
    END IF;
    IF NOT (v_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB) THEN
        RAISE EXCEPTION 'RUNTIME_OWNER_RESTORE_STATE_MISMATCH' USING ERRCODE = '55000';
    END IF;
    v_context := v_task.delivery_context || '{"actor":true,"runtime":false}'::JSONB;
    UPDATE tasks SET delivery_context = v_context WHERE id = p_task_id;
    RETURN jsonb_build_object('outcome', 'restored', 'task_id', p_task_id);
END $$;

CREATE OR REPLACE FUNCTION discover_generation_turn_candidates(p_limit INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_candidates JSONB;
BEGIN
    PERFORM public._assert_actor_worker_discovery_scope();
    IF p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'ACTOR_DISCOVERY_LIMIT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'task_id', candidate.id,
        'conversation_id', candidate.conversation_id,
        'execution_mode', candidate.execution_mode
    ) ORDER BY candidate.queue_sequence, candidate.id), '[]'::JSONB)
    INTO v_candidates
    FROM (
        SELECT task.id, task.conversation_id, task.execution_mode,
               task.queue_sequence
        FROM public.tasks task
        WHERE task.type = 'chat'
          AND task.delivery_context @> '{"actor": true}'::JSONB
          AND task.status IN ('pending', 'running')
          AND task.conversation_id IS NOT NULL
          AND task.execution_mode IN ('serial', 'branch')
        ORDER BY task.queue_sequence, task.id
        LIMIT p_limit
    ) candidate;
    RETURN v_candidates;
END;
$$;

CREATE OR REPLACE FUNCTION _assert_actor_worker_task_scope(p_task_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_task public.tasks%ROWTYPE;
BEGIN
    IF p_task_id IS NULL OR session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'worker'
       OR NULLIF(current_setting('app.request_id', TRUE), '') IS NULL THEN
        RAISE EXCEPTION 'ACTOR_WORKER_TASK_SCOPE_REQUIRED' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id = p_task_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002'; END IF;
    IF NULLIF(current_setting('app.actor_user_id', TRUE), '')::UUID IS DISTINCT FROM v_task.user_id
       OR NULLIF(current_setting('app.org_id', TRUE), '')::UUID IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_WORKER_TASK_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
END;
$$;

RESET ROLE;
