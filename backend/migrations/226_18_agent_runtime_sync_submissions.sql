-- 226_18: durable Sync submission identity and recovery mapping.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_sync_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    scope_id TEXT NOT NULL,
    sync_domain TEXT NOT NULL,
    external_idempotency_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    provider_task_ref TEXT,
    submission_state TEXT NOT NULL CHECK (submission_state IN ('intent','found','proven_not_submitted','unknown')),
    state_version BIGINT NOT NULL DEFAULT 0,
    enqueue_checkpoint JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(action_id, attempt_id, sync_domain),
    UNIQUE(provider, provider_task_ref)
);
ALTER TABLE agent_sync_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_sync_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_sync_submissions_owner_all ON agent_sync_submissions FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE agent_sync_submissions FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai_runtime;

CREATE FUNCTION create_or_get_agent_sync_submission(
    p_action_id UUID, p_attempt_id UUID, p_request_hash TEXT, p_scope_id TEXT,
    p_sync_domain TEXT, p_external_idempotency_key TEXT, p_provider TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_actions%ROWTYPE; t agent_action_attempts%ROWTYPE; s agent_sync_submissions%ROWTYPE;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  SELECT * INTO a FROM agent_actions WHERE id=p_action_id;
  SELECT * INTO t FROM agent_action_attempts WHERE id=p_attempt_id AND action_id=p_action_id;
  IF a.id IS NULL OR t.id IS NULL OR p_request_hash IS DISTINCT FROM a.request_hash OR p_request_hash IS DISTINCT FROM t.request_hash THEN
    RAISE EXCEPTION 'AGENT_SYNC_SUBMISSION_BINDING_INVALID' USING ERRCODE='22023';
  END IF;
  SELECT * INTO s FROM agent_sync_submissions WHERE external_idempotency_key=p_external_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF s.action_id IS DISTINCT FROM p_action_id OR s.attempt_id IS DISTINCT FROM p_attempt_id OR s.request_hash IS DISTINCT FROM p_request_hash OR s.scope_id IS DISTINCT FROM p_scope_id OR s.sync_domain IS DISTINCT FROM p_sync_domain OR s.provider IS DISTINCT FROM p_provider THEN
      RAISE EXCEPTION 'AGENT_SYNC_SUBMISSION_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('outcome','readback','submission_id',s.id,'provider_task_ref',s.provider_task_ref,'submission_state',s.submission_state,'state_version',s.state_version,'external_idempotency_key',s.external_idempotency_key);
  END IF;
  BEGIN
    INSERT INTO agent_sync_submissions(action_id,attempt_id,org_id,user_id,request_hash,scope_id,sync_domain,external_idempotency_key,provider,submission_state)
      VALUES(a.id,t.id,a.org_id,a.user_id,p_request_hash,p_scope_id,p_sync_domain,p_external_idempotency_key,p_provider,'intent') RETURNING * INTO s;
  EXCEPTION WHEN unique_violation THEN
    SELECT * INTO s FROM agent_sync_submissions WHERE external_idempotency_key=p_external_idempotency_key FOR UPDATE;
    IF s.id IS NULL OR s.action_id IS DISTINCT FROM p_action_id OR s.attempt_id IS DISTINCT FROM p_attempt_id OR s.request_hash IS DISTINCT FROM p_request_hash OR s.scope_id IS DISTINCT FROM p_scope_id OR s.sync_domain IS DISTINCT FROM p_sync_domain OR s.provider IS DISTINCT FROM p_provider THEN
      RAISE EXCEPTION 'AGENT_SYNC_SUBMISSION_IDEMPOTENCY_CONFLICT' USING ERRCODE='23505';
    END IF;
    RETURN jsonb_build_object('outcome','readback','submission_id',s.id,'provider_task_ref',s.provider_task_ref,'submission_state',s.submission_state,'state_version',s.state_version,'external_idempotency_key',s.external_idempotency_key);
  END;
  RETURN jsonb_build_object('outcome','created','submission_id',s.id,'submission_state',s.submission_state,'state_version',s.state_version,'external_idempotency_key',s.external_idempotency_key);
END; $$;

CREATE FUNCTION record_agent_sync_submission_result(
    p_submission_id UUID, p_external_idempotency_key TEXT, p_request_hash TEXT,
    p_provider_task_ref TEXT, p_submission_state TEXT, p_enqueue_checkpoint JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s agent_sync_submissions%ROWTYPE;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  SELECT * INTO s FROM agent_sync_submissions WHERE id=p_submission_id AND external_idempotency_key=p_external_idempotency_key FOR UPDATE;
  IF NOT FOUND OR s.request_hash IS DISTINCT FROM p_request_hash THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  IF p_submission_state NOT IN ('found','proven_not_submitted','unknown') THEN RAISE EXCEPTION 'AGENT_SYNC_SUBMISSION_STATE_INVALID' USING ERRCODE='22023'; END IF;
  IF s.provider_task_ref IS NOT NULL AND p_provider_task_ref IS DISTINCT FROM s.provider_task_ref THEN RAISE EXCEPTION 'AGENT_SYNC_SUBMISSION_REF_CONFLICT' USING ERRCODE='23505'; END IF;
  UPDATE agent_sync_submissions SET provider_task_ref=COALESCE(s.provider_task_ref,NULLIF(btrim(p_provider_task_ref),'')), submission_state=p_submission_state, state_version=state_version+1, enqueue_checkpoint=COALESCE(p_enqueue_checkpoint,'{}'), updated_at=clock_timestamp() WHERE id=s.id RETURNING * INTO s;
  RETURN jsonb_build_object('outcome','recorded','submission_id',s.id,'provider_task_ref',s.provider_task_ref,'submission_state',s.submission_state,'state_version',s.state_version);
END; $$;

CREATE FUNCTION recover_agent_sync_submission(p_external_idempotency_key TEXT, p_request_hash TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s agent_sync_submissions%ROWTYPE;
BEGIN
  PERFORM _assert_agent_runtime_actor(TRUE);
  SELECT * INTO s FROM agent_sync_submissions WHERE external_idempotency_key=p_external_idempotency_key FOR SHARE;
  IF NOT FOUND OR s.request_hash IS DISTINCT FROM p_request_hash THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
  RETURN jsonb_build_object('outcome',CASE WHEN s.provider_task_ref IS NOT NULL THEN 'found' WHEN s.submission_state='proven_not_submitted' THEN 'proven_not_submitted' ELSE 'unknown' END,'submission_id',s.id,'provider_task_ref',s.provider_task_ref,'submission_state',s.submission_state,'state_version',s.state_version);
END; $$;
REVOKE ALL ON FUNCTION create_or_get_agent_sync_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT),record_agent_sync_submission_result(UUID,TEXT,TEXT,TEXT,TEXT,JSONB),recover_agent_sync_submission(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_or_get_agent_sync_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT),record_agent_sync_submission_result(UUID,TEXT,TEXT,TEXT,TEXT,JSONB),recover_agent_sync_submission(TEXT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
