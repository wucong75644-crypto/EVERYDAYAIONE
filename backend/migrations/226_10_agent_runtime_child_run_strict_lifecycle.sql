-- 226_10: strict Child Run context, fencing, aggregation and cancellation.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION create_agent_child_run_strict(
    p_parent_run_id UUID, p_parent_action_id UUID, p_parent_request_hash TEXT,
    p_parent_execution_token UUID, p_child_ordinal INTEGER, p_capability TEXT,
    p_context JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE p agent_runs%ROWTYPE; a agent_actions%ROWTYPE; r agent_policy_receipts%ROWTYPE;
        scope_ok BOOLEAN;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO p FROM agent_runs WHERE id=p_parent_run_id;
    SELECT * INTO a FROM agent_actions WHERE id=p_parent_action_id;
    IF NOT FOUND OR a.run_id IS DISTINCT FROM p_parent_run_id THEN
        RAISE EXCEPTION 'AGENT_CHILD_PARENT_BINDING_INVALID' USING ERRCODE='22023';
    END IF;
    IF p_parent_request_hash IS DISTINCT FROM a.request_hash
       OR p_parent_execution_token IS NULL
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
    scope_ok := (p_context->'scope'->>'org_id') IS NOT DISTINCT FROM p.org_id::TEXT
        AND (p_context->'scope'->>'user_id') IS NOT DISTINCT FROM p.user_id::TEXT;
    IF NOT scope_ok THEN RAISE EXCEPTION 'AGENT_CHILD_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
    RETURN create_agent_child_run(p_parent_run_id,p_parent_action_id,p_parent_request_hash,
        p_child_ordinal,p_capability,p_context);
END; $$;

CREATE FUNCTION complete_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_aggregation_revision INTEGER, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE; a agent_actions%ROWTYPE; pending INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id AND parent_run_id=p_parent_run_id
        AND parent_action_id=p_parent_action_id AND parent_request_hash=p_parent_request_hash FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    SELECT count(*) INTO pending FROM agent_actions WHERE run_id=c.id
        AND status NOT IN ('completed','failed','rejected','cancelled');
    IF pending > 0 OR c.status NOT IN ('queued','running','waiting_actions','accepted') THEN
        RETURN jsonb_build_object('outcome','child_not_terminal','pending_actions',pending,'status',c.status);
    END IF;
    RETURN complete_agent_child_run(c.id,p_parent_run_id,p_aggregation_revision,p_result);
END; $$;

CREATE FUNCTION read_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id AND parent_run_id=p_parent_run_id
        AND parent_action_id=p_parent_action_id AND parent_request_hash=p_parent_request_hash;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
    RETURN jsonb_build_object('outcome','readback','child_run_id',c.id,'parent_run_id',c.parent_run_id,
        'status',c.status,'aggregation_revision',c.aggregation_revision,'result_hash',c.result_hash);
END; $$;

CREATE FUNCTION cancel_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE c agent_runs%ROWTYPE; changed INTEGER;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id AND parent_run_id=p_parent_run_id
        AND parent_action_id=p_parent_action_id AND parent_request_hash=p_parent_request_hash FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    UPDATE agent_actions SET status='cancelled',completed_at=clock_timestamp(),state_version=state_version+1,updated_at=clock_timestamp()
      WHERE run_id=c.id AND status IN ('requested','queued','running','claimed');
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF c.status NOT IN ('completed','failed','cancelled') THEN
        UPDATE agent_runs SET status='cancelled',terminal_reason=p_reason,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=c.id;
        RETURN jsonb_build_object('outcome','cancelled','propagated_actions',changed);
    END IF;
    RETURN jsonb_build_object('outcome',c.status,'propagated_actions',changed);
END; $$;

REVOKE ALL ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),read_agent_child_run_strict(UUID,UUID,UUID,TEXT),complete_agent_child_run_strict(UUID,UUID,UUID,TEXT,INTEGER,JSONB),cancel_agent_child_run_strict(UUID,UUID,UUID,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),read_agent_child_run_strict(UUID,UUID,UUID,TEXT),complete_agent_child_run_strict(UUID,UUID,UUID,TEXT,INTEGER,JSONB),cancel_agent_child_run_strict(UUID,UUID,UUID,TEXT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
