-- C7-B3.2-A: attempt-bound SAFE/NONE PolicyReceipt activation.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_safe_action_activations(
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action_id UUID NOT NULL UNIQUE REFERENCES agent_actions(id) ON DELETE RESTRICT,
  attempt_id UUID NOT NULL UNIQUE REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
  policy_receipt_id UUID NOT NULL UNIQUE REFERENCES agent_policy_receipts(id) ON DELETE RESTRICT,
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
  execution_token UUID NOT NULL,
  request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
  catalog_revision TEXT NOT NULL CHECK(catalog_revision~'^[0-9a-f]{64}$'),
  effective_toolset_hash TEXT NOT NULL CHECK(effective_toolset_hash~'^[0-9a-f]{64}$'),
  tool_name TEXT NOT NULL CHECK(tool_name=lower(btrim(tool_name))),
  schema_hash TEXT NOT NULL CHECK(schema_hash~'^[0-9a-f]{64}$'),
  executor_type TEXT NOT NULL CHECK(length(btrim(executor_type)) BETWEEN 1 AND 200),
  executor_revision INTEGER NOT NULL CHECK(executor_revision>0),
  policy_revision TEXT NOT NULL CHECK(length(btrim(policy_revision)) BETWEEN 1 AND 200),
  capability TEXT NOT NULL CHECK(length(btrim(capability)) BETWEEN 1 AND 200),
  tenant_kill_epoch BIGINT NOT NULL CHECK(tenant_kill_epoch>=0),
  capability_kill_epoch BIGINT NOT NULL CHECK(capability_kill_epoch>=0),
  attempt_state_version BIGINT NOT NULL CHECK(attempt_state_version>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE agent_safe_action_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_safe_action_activations FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_safe_action_activations_owner_all
 ON agent_safe_action_activations FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);

CREATE FUNCTION _agent_safe_activation_immutable()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
  RAISE EXCEPTION 'AGENT_SAFE_ACTION_ACTIVATION_IMMUTABLE'
    USING ERRCODE='55000';
END $$;
CREATE TRIGGER trg_agent_safe_activation_immutable
 BEFORE UPDATE OR DELETE ON agent_safe_action_activations
 FOR EACH ROW EXECUTE FUNCTION _agent_safe_activation_immutable();

CREATE FUNCTION activate_agent_safe_action(
 p_attempt_id UUID,p_execution_token UUID,p_expected_attempt_version BIGINT,
 p_request_hash TEXT,p_executor_type TEXT,p_executor_revision INTEGER,
 p_policy_revision TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
  v_attempt agent_action_attempts%ROWTYPE;
  v_action agent_actions%ROWTYPE;
  v_run agent_runs%ROWTYPE;
  v_session agent_runtime_sessions%ROWTYPE;
  v_fence agent_runtime_owner_fences%ROWTYPE;
  v_activation agent_safe_action_activations%ROWTYPE;
  v_toolset agent_runtime_effective_toolset_facts%ROWTYPE;
  v_tool JSONB;
  v_capability TEXT;
  v_fence_result JSONB;
  v_receipt_result JSONB;
  v_receipt JSONB;
  v_receipt_hash TEXT;
BEGIN
  IF NOT (
    (session_user='everydayai_agent_runtime_worker'
      AND current_setting('app.access_kind',true)='agent_runtime') OR
    (session_user='everydayai_authorization_worker'
      AND current_setting('app.access_kind',true)='authorization')
  ) THEN
    RAISE EXCEPTION 'AGENT_SAFE_AUTHORIZATION_OWNER_REQUIRED'
      USING ERRCODE='42501';
  END IF;
  PERFORM _assert_agent_runtime_actor(TRUE);
  IF p_request_hash!~'^[0-9a-f]{64}$' OR p_executor_revision<1
     OR NULLIF(btrim(p_executor_type),'') IS NULL
     OR NULLIF(btrim(p_policy_revision),'') IS NULL THEN
    RAISE EXCEPTION 'AGENT_SAFE_ACTIVATION_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO v_attempt FROM agent_action_attempts
   WHERE id=p_attempt_id FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
  SELECT * INTO v_action FROM agent_actions
   WHERE id=v_attempt.action_id FOR UPDATE;
  SELECT * INTO v_run FROM agent_runs WHERE id=v_action.run_id FOR UPDATE;
  SELECT * INTO v_session FROM agent_runtime_sessions
   WHERE id=v_action.session_id FOR UPDATE;
  IF v_attempt.execution_token IS DISTINCT FROM p_execution_token THEN
    RETURN jsonb_build_object('outcome','ownership_lost');
  END IF;
  IF v_attempt.state_version IS DISTINCT FROM p_expected_attempt_version
     OR v_attempt.status<>'claimed' OR v_action.status<>'running' THEN
    RETURN jsonb_build_object('outcome','stale_version');
  END IF;
  IF v_attempt.request_hash IS DISTINCT FROM p_request_hash
     OR v_action.request_hash IS DISTINCT FROM p_request_hash THEN
    RETURN jsonb_build_object('outcome','request_hash_conflict');
  END IF;
  IF v_action.org_id IS NULL OR v_action.org_id IS DISTINCT FROM v_run.org_id
     OR v_action.org_id IS DISTINCT FROM v_session.org_id
     OR v_attempt.org_id IS DISTINCT FROM v_action.org_id
     OR v_attempt.run_id IS DISTINCT FROM v_action.run_id THEN
    RETURN jsonb_build_object('outcome','scope_mismatch');
  END IF;
  SELECT * INTO v_activation FROM agent_safe_action_activations
   WHERE action_id=v_action.id FOR UPDATE;
  IF FOUND THEN
    IF v_activation.attempt_id IS DISTINCT FROM v_attempt.id
       OR v_activation.execution_token IS DISTINCT FROM p_execution_token
       OR v_activation.request_hash IS DISTINCT FROM p_request_hash
       OR v_activation.executor_type IS DISTINCT FROM btrim(p_executor_type)
       OR v_activation.executor_revision IS DISTINCT FROM p_executor_revision
       OR v_activation.policy_revision IS DISTINCT FROM btrim(p_policy_revision) THEN
      RETURN jsonb_build_object('outcome','activation_conflict');
    END IF;
    RETURN jsonb_build_object(
      'outcome','already_activated',
      'policy_receipt_id',v_activation.policy_receipt_id);
  END IF;
  IF v_action.policy_decision<>'preauthorized'
     OR v_action.policy_snapshot->>'safety_level'<>'safe'
     OR v_action.policy_snapshot->>'side_effect'<>'none'
     OR v_action.policy_snapshot->>'authorization_requirement'<>'none'
     OR v_action.policy_revision IS DISTINCT FROM btrim(p_policy_revision) THEN
    RETURN jsonb_build_object('outcome','safe_policy_required');
  END IF;
  SELECT * INTO v_toolset
    FROM agent_runtime_effective_toolset_facts toolset
   WHERE toolset.effective_toolset_hash=
           v_run.capability_snapshot->>'effective_toolset_hash'
     AND toolset.catalog_revision=
           v_run.capability_snapshot->>'effective_toolset_revision'
     AND toolset.scope_kind=v_session.scope_kind
     AND toolset.channel=v_run.capability_snapshot->>'channel'
     AND toolset.recoverable;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('outcome','toolset_mismatch');
  END IF;
  SELECT tool.value INTO v_tool
    FROM jsonb_array_elements(v_toolset.toolset_document->'tools') tool(value)
   WHERE tool.value->>'canonical_name'=v_action.tool_name;
  IF NOT FOUND OR v_tool->>'safety_level'<>'safe'
     OR v_tool->>'side_effect'<>'none'
     OR v_tool->>'authorization_requirement'<>'none'
     OR v_tool->>'executor_type' IS DISTINCT FROM btrim(p_executor_type)
     OR (v_tool->>'executor_revision')::INTEGER IS DISTINCT FROM p_executor_revision
     OR v_tool->>'schema_hash' IS DISTINCT FROM v_action.policy_snapshot->>'schema_hash'
     OR v_toolset.catalog_revision IS DISTINCT FROM
          v_action.policy_snapshot->>'catalog_revision'
     OR v_toolset.effective_toolset_hash IS DISTINCT FROM
          v_action.policy_snapshot->>'effective_toolset_hash' THEN
    RETURN jsonb_build_object('outcome','toolset_mismatch');
  END IF;
  IF jsonb_array_length(v_tool->'capability_requirements')<>1 THEN
    RETURN jsonb_build_object('outcome','capability_mismatch');
  END IF;
  v_capability:=v_tool->'capability_requirements'->>0;
  IF v_action.policy_snapshot->'capability_requirements' IS DISTINCT FROM
       v_tool->'capability_requirements'
     OR v_action.policy_snapshot->>'capability_revision' IS DISTINCT FROM
       v_tool->>'schema_hash' THEN
    RETURN jsonb_build_object('outcome','capability_mismatch');
  END IF;
  v_fence_result:=_agent_runtime_kill_epoch_context(
    p_attempt_id,p_execution_token,p_request_hash,
    p_expected_attempt_version,'dispatch');
  IF v_fence_result->>'outcome'<>'allowed' THEN RETURN v_fence_result; END IF;
  SELECT * INTO v_fence FROM agent_runtime_owner_fences
   WHERE owner_kind='attempt' AND owner_id=v_attempt.id
     AND execution_token=p_execution_token FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('outcome','owner_fence_missing');
  END IF;
  v_receipt_hash:=encode(digest(convert_to(jsonb_build_object(
    'org_id',v_action.org_id,'run_id',v_action.run_id,
    'action_id',v_action.id,'attempt_id',v_attempt.id,
    'request_hash',p_request_hash,'execution_token',p_execution_token,
    'catalog_revision',v_toolset.catalog_revision,
    'effective_toolset_hash',v_toolset.effective_toolset_hash,
    'executor_type',btrim(p_executor_type),
    'executor_revision',p_executor_revision,
    'policy_revision',btrim(p_policy_revision),
    'tenant_kill_epoch',v_fence.tenant_kill_epoch,
    'capability',v_capability,
    'capability_kill_epoch',v_fence.capability_kill_epoch
  )::TEXT,'UTF8'),'sha256'),'hex');
  v_receipt_result:=record_agent_policy_receipt(
    v_action.id,v_action.arguments_hash,btrim(p_executor_type),
    p_executor_revision,btrim(p_policy_revision),'allow',NULL,
    jsonb_build_object(
      'org_id',v_action.org_id,'run_id',v_action.run_id,
      'action_id',v_action.id,'attempt_id',v_attempt.id,
      'request_hash',p_request_hash,
      'catalog_revision',v_toolset.catalog_revision,
      'effective_toolset_hash',v_toolset.effective_toolset_hash,
      'capability',v_capability,
      'tenant_kill_epoch',v_fence.tenant_kill_epoch,
      'capability_kill_epoch',v_fence.capability_kill_epoch),
    ARRAY['NO_AUTHORIZATION_REQUIRED','SAFE_LOCAL_READ'],ARRAY['audit'],
    v_receipt_hash,300);
  IF v_receipt_result->>'outcome' NOT IN ('recorded','already_recorded') THEN
    RETURN v_receipt_result;
  END IF;
  v_receipt:=v_receipt_result->'receipt';
  INSERT INTO agent_safe_action_activations(
    action_id,attempt_id,policy_receipt_id,org_id,run_id,execution_token,
    request_hash,catalog_revision,effective_toolset_hash,tool_name,schema_hash,
    executor_type,executor_revision,policy_revision,capability,
    tenant_kill_epoch,capability_kill_epoch,attempt_state_version
  ) VALUES(
    v_action.id,v_attempt.id,(v_receipt->>'id')::UUID,v_action.org_id,
    v_action.run_id,p_execution_token,p_request_hash,v_toolset.catalog_revision,
    v_toolset.effective_toolset_hash,v_action.tool_name,v_tool->>'schema_hash',
    btrim(p_executor_type),p_executor_revision,btrim(p_policy_revision),
    v_capability,v_fence.tenant_kill_epoch,v_fence.capability_kill_epoch,
    p_expected_attempt_version
  ) RETURNING * INTO v_activation;
  RETURN jsonb_build_object(
    'outcome','activated','policy_receipt_id',v_activation.policy_receipt_id);
END $$;

REVOKE ALL ON TABLE agent_safe_action_activations FROM PUBLIC,
 everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_authorization_worker;
REVOKE ALL ON FUNCTION
 _agent_safe_activation_immutable(),
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_projection_worker,
 everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT)
TO everydayai_agent_runtime_worker,everydayai_authorization_worker;

RESET ROLE;
