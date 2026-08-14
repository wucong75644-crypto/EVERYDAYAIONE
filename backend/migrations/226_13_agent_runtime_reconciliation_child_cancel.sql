-- 226_13: non-terminal reconciliation, Child Run v2 readback and cancel parity.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_runs ADD COLUMN child_terminal_result JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(child_terminal_result)='object');

CREATE FUNCTION record_agent_action_provider_still_accepted(
    p_attempt_id UUID, p_reconciliation_token UUID, p_expected_state_version BIGINT,
    p_request_hash TEXT, p_provider_receipt JSONB, p_next_reconcile_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR a.request_hash IS DISTINCT FROM p_request_hash
       OR a.status IS DISTINCT FROM 'accepted'
       OR a.reconciliation_lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),
      last_provider_status='accepted', next_reconcile_at=COALESCE(p_next_reconcile_at,clock_timestamp()+interval '60 seconds'),
      reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,state_version=state_version+1,updated_at=clock_timestamp()
      WHERE id=a.id;
    RETURN jsonb_build_object('outcome','still_accepted','attempt_id',a.id);
END; $$;

CREATE FUNCTION record_agent_action_provider_still_unknown(
    p_attempt_id UUID, p_reconciliation_token UUID, p_expected_state_version BIGINT,
    p_request_hash TEXT, p_provider_receipt JSONB, p_ambiguity_evidence JSONB,
    p_next_reconcile_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
    IF NOT FOUND OR a.reconciliation_token IS DISTINCT FROM p_reconciliation_token
       OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR a.request_hash IS DISTINCT FROM p_request_hash
       OR a.status IS DISTINCT FROM 'unknown'
       OR a.reconciliation_lease_expires_at <= clock_timestamp()
       OR jsonb_typeof(COALESCE(p_ambiguity_evidence,'{}')) IS DISTINCT FROM 'object'
       OR COALESCE(p_ambiguity_evidence,'{}')='{}' THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),
      ambiguity_evidence=p_ambiguity_evidence,last_provider_status='unknown',
      next_reconcile_at=COALESCE(p_next_reconcile_at,clock_timestamp()+interval '60 seconds'),
      reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,state_version=state_version+1,updated_at=clock_timestamp()
      WHERE id=a.id;
    RETURN jsonb_build_object('outcome','still_unknown','attempt_id',a.id);
END; $$;

CREATE FUNCTION read_agent_child_run_strict_v2(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_attempt_id UUID, p_parent_request_hash TEXT,
    p_ownership_token UUID, p_expected_state_version INTEGER,
    p_child_ordinal INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c agent_runs%ROWTYPE; a agent_action_attempts%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id
      AND parent_run_id=p_parent_run_id AND parent_action_id=p_parent_action_id
      AND parent_request_hash=p_parent_request_hash AND child_ordinal=p_child_ordinal;
    SELECT * INTO a FROM agent_action_attempts WHERE id=p_parent_attempt_id AND action_id=p_parent_action_id;
    IF NOT FOUND OR a.request_hash IS DISTINCT FROM p_parent_request_hash
       OR a.state_version IS DISTINCT FROM p_expected_state_version
       OR (a.execution_token IS DISTINCT FROM p_ownership_token AND a.reconciliation_token IS DISTINCT FROM p_ownership_token) THEN
        RETURN jsonb_build_object('outcome','fenced');
    END IF;
    IF c.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    RETURN jsonb_build_object('outcome','readback','child_run_id',c.id,
      'parent_run_id',c.parent_run_id,'parent_action_id',c.parent_action_id,
      'child_ordinal',c.child_ordinal,'status',c.status,'state_version',c.state_version,
      'aggregation_revision',c.aggregation_revision,'context_receipt',c.context_receipt,
      'result_hash',c.result_hash,'result',c.child_terminal_result);
END; $$;

CREATE OR REPLACE FUNCTION finalize_agent_action_provider_v2(
    p_attempt_id UUID, p_execution_token UUID, p_reconciliation_token UUID,
    p_expected_state_version INTEGER, p_request_hash TEXT, p_terminal_state TEXT,
    p_provider_receipt JSONB, p_result JSONB, p_cost_kind TEXT,
    p_reserved_amount BIGINT, p_actual_amount BIGINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE a agent_action_attempts%ROWTYPE; act agent_actions%ROWTYPE;
  cost_result JSONB; terminal_result JSONB; effective_token UUID;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  IF p_terminal_state NOT IN ('completed','failed','cancelled') OR p_provider_receipt_hash IS NULL
     OR p_provider_receipt_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_CONTRACT_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO a FROM agent_action_attempts WHERE id=p_attempt_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_FINALIZE_ATTEMPT_NOT_FOUND'; END IF;
  SELECT * INTO act FROM agent_actions WHERE id=a.action_id FOR UPDATE;
  IF p_terminal_state='cancelled' AND a.status='cancelled' AND act.status='cancelled' THEN
    RETURN jsonb_build_object('outcome','already_cancelled','action_id',a.action_id);
  END IF;
  effective_token:=CASE WHEN a.status IN ('accepted','unknown') THEN p_reconciliation_token ELSE p_execution_token END;
  IF effective_token IS NULL OR a.state_version IS DISTINCT FROM p_expected_state_version
     OR (a.status IN ('accepted','unknown') AND a.reconciliation_token IS DISTINCT FROM effective_token)
     OR (a.status NOT IN ('accepted','unknown') AND a.execution_token IS DISTINCT FROM effective_token)
     OR a.request_hash IS DISTINCT FROM p_request_hash THEN RAISE EXCEPTION 'AGENT_FINALIZE_FENCED' USING ERRCODE='42501'; END IF;
  IF a.status NOT IN ('dispatching','accepted','unknown') OR act.status NOT IN ('running','accepted','unknown') THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM agent_action_dispatch_intents i JOIN agent_policy_receipts r ON r.id=i.policy_receipt_id
    WHERE i.attempt_id=a.id AND i.action_id=a.action_id AND i.request_hash=p_request_hash
      AND i.execution_token=a.execution_token AND r.action_id=a.action_id AND r.decision='allow') THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_DISPATCH_CONTRACT_MISSING' USING ERRCODE='42501';
  END IF;
  UPDATE agent_action_attempts SET external_receipt=COALESCE(p_provider_receipt,'{}'),last_provider_status=p_terminal_state,updated_at=clock_timestamp() WHERE id=a.id;
  IF p_cost_kind IS NOT NULL THEN
    SELECT record_agent_action_cost_strict(a.action_id,a.id,p_cost_kind,p_reserved_amount,p_actual_amount,p_currency,p_reason_code,p_provider_receipt_hash) INTO cost_result;
  END IF;
  SELECT _finish_agent_action(a.id,effective_token,a.state_version,p_request_hash,p_terminal_state,
    COALESCE(p_result,jsonb_build_object('status',p_terminal_state,'external_receipt',p_provider_receipt))) INTO terminal_result;
  IF terminal_result->>'outcome' NOT IN (p_terminal_state,'already_'||p_terminal_state) THEN
    RAISE EXCEPTION 'AGENT_FINALIZE_TERMINAL_CONFLICT' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object('outcome',p_terminal_state,'cost',COALESCE(cost_result,'{}'),'terminal',terminal_result);
END; $$;

CREATE OR REPLACE FUNCTION aggregate_agent_child_run_strict(
    p_child_run_id UUID, p_parent_run_id UUID, p_parent_action_id UUID,
    p_parent_request_hash TEXT, p_parent_attempt_id UUID,
    p_reconciliation_token UUID, p_expected_state_version INTEGER,
    p_aggregation_revision INTEGER, p_result JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE c agent_runs%ROWTYPE; pending INTEGER; outcome JSONB;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  SELECT * INTO c FROM agent_runs WHERE id=p_child_run_id AND parent_run_id=p_parent_run_id
    AND parent_action_id=p_parent_action_id AND parent_request_hash=p_parent_request_hash FOR UPDATE;
  IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM agent_action_attempts a WHERE a.id=p_parent_attempt_id
    AND a.action_id=p_parent_action_id AND a.request_hash=p_parent_request_hash
    AND (a.execution_token=p_reconciliation_token OR a.reconciliation_token=p_reconciliation_token)
    AND a.state_version=p_expected_state_version) THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  SELECT count(*) INTO pending FROM agent_actions WHERE run_id=c.id AND status NOT IN ('completed','failed','rejected','cancelled');
  IF pending>0 OR c.status NOT IN ('completed','failed','cancelled') THEN
    RETURN jsonb_build_object('outcome','child_not_terminal','pending_actions',pending,'status',c.status);
  END IF;
  SELECT complete_agent_child_run(c.id,p_parent_run_id,p_aggregation_revision,p_result) INTO outcome;
  IF outcome->>'outcome'='completed' THEN UPDATE agent_runs SET child_terminal_result=COALESCE(p_result,'{}') WHERE id=c.id; END IF;
  RETURN outcome;
END; $$;

REVOKE ALL ON FUNCTION record_agent_action_provider_still_accepted(UUID,UUID,BIGINT,TEXT,JSONB,TIMESTAMPTZ), record_agent_action_provider_still_unknown(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ), read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER), finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_action_provider_still_accepted(UUID,UUID,BIGINT,TEXT,JSONB,TIMESTAMPTZ), record_agent_action_provider_still_unknown(UUID,UUID,BIGINT,TEXT,JSONB,JSONB,TIMESTAMPTZ), read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER), finalize_agent_action_provider_v2(UUID,UUID,UUID,INTEGER,TEXT,TEXT,JSONB,JSONB,TEXT,BIGINT,BIGINT,TEXT,TEXT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
