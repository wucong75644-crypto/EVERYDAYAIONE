-- 226_17: make Child Run ordinal uniqueness enforceable under concurrency.
SET LOCAL ROLE everydayai_owner;
CREATE UNIQUE INDEX uq_agent_child_parent_ordinal
    ON agent_runs(parent_action_id, child_ordinal)
    WHERE parent_action_id IS NOT NULL AND child_ordinal IS NOT NULL;

CREATE OR REPLACE FUNCTION create_agent_child_run_strict(
    p_parent_run_id UUID, p_parent_action_id UUID, p_parent_request_hash TEXT,
    p_parent_execution_token UUID, p_child_ordinal INTEGER, p_capability TEXT,
    p_context JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE p agent_runs%ROWTYPE; a agent_actions%ROWTYPE; r agent_policy_receipts%ROWTYPE; child_command UUID;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO p FROM agent_runs WHERE id=p_parent_run_id;
    SELECT * INTO a FROM agent_actions WHERE id=p_parent_action_id;
    IF NOT FOUND OR a.run_id IS DISTINCT FROM p_parent_run_id
       OR p_parent_request_hash IS DISTINCT FROM a.request_hash
       OR NOT EXISTS (SELECT 1 FROM agent_action_attempts x WHERE x.action_id=a.id
          AND x.execution_token=p_parent_execution_token AND x.status IN ('dispatching','accepted','unknown')) THEN
        RAISE EXCEPTION 'AGENT_CHILD_PARENT_FENCED' USING ERRCODE='42501';
    END IF;
    IF jsonb_typeof(p_context) IS DISTINCT FROM 'object'
       OR NOT (p_context ? 'policy_receipt_id') OR NOT (p_context ? 'capability')
       OR NOT (p_context ? 'budget_remaining') OR NOT (p_context ? 'scope') THEN
        RAISE EXCEPTION 'AGENT_CHILD_CONTEXT_REQUIRED' USING ERRCODE='22023';
    END IF;
    SELECT * INTO r FROM agent_policy_receipts WHERE id=(p_context->>'policy_receipt_id')::UUID
      AND action_id=a.id AND decision='allow' AND expires_at > clock_timestamp();
    IF NOT FOUND OR p_context->>'capability' IS DISTINCT FROM p_capability
       OR (p_context->>'budget_remaining')::NUMERIC < 0 THEN
        RAISE EXCEPTION 'AGENT_CHILD_POLICY_CONTEXT_INVALID' USING ERRCODE='42501';
    END IF;
    BEGIN
        INSERT INTO agent_session_commands(session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash)
        VALUES(p.session_id,p.org_id,p.user_id,'submit_input','child:'||a.id::TEXT||':'||p_child_ordinal,
          jsonb_build_object('parent_run_id',p.id,'parent_action_id',a.id,'request_hash',p_parent_request_hash,'capability',p_capability),
          left(encode(digest(convert_to(p_parent_request_hash,'UTF8'),'sha256'),'hex'),32))
        RETURNING id INTO child_command;
        INSERT INTO agent_runs(session_id,command_id,org_id,user_id,run_kind,status,idempotency_key,request_hash,context_receipt,config_snapshot,capability_snapshot,parent_run_id,parent_action_id,child_ordinal,parent_request_hash)
        VALUES(p.session_id,child_command,p.org_id,p.user_id,'continuation','queued','child:'||a.id::TEXT||':'||p_child_ordinal,
          left(encode(digest(convert_to(p_parent_request_hash,'UTF8'),'sha256'),'hex'),32),p_context,p.config_snapshot,p.capability_snapshot,p.id,a.id,p_child_ordinal,p_parent_request_hash)
        RETURNING * INTO p;
        PERFORM _agent_runtime_226_append_action_event(a.id,'action.child_run.created',jsonb_build_object('child_run_id',p.id,'child_ordinal',p_child_ordinal));
        RETURN jsonb_build_object('outcome','created','child_run_id',p.id,'parent_run_id',p_parent_run_id,'parent_action_id',a.id);
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO p FROM agent_runs WHERE parent_action_id=p_parent_action_id
          AND child_ordinal=p_child_ordinal;
        IF p.id IS NULL THEN RAISE; END IF;
        RETURN jsonb_build_object('outcome','already_exists','child_run_id',p.id,
            'parent_run_id',p.parent_run_id,'parent_action_id',p.parent_action_id);
    END;
END; $$;
REVOKE ALL ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB) TO everydayai_agent_runtime_worker;
RESET ROLE;
