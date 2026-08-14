-- 228.01: Canonicalize raw ModelLoop Action hashes inside PostgreSQL.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION complete_model_attempt_with_raw_actions(
 p_attempt_id UUID,p_run_execution_token UUID,
 p_expected_attempt_version BIGINT,p_expected_step_version BIGINT,
 p_request_hash TEXT,p_response_receipt JSONB,p_response_hash TEXT,
 p_provider_stop_reason TEXT,p_usage JSONB,p_actual_credits INTEGER,
 p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE step agent_model_steps%ROWTYPE; canonical JSONB; batch_hash TEXT;
 canonical_actions JSONB; result JSONB; action agent_actions%ROWTYPE;
 prompt JSONB; opened JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT ms.* INTO step FROM agent_model_steps ms
  JOIN agent_model_attempts ma ON ma.model_step_id=ms.id
  WHERE ma.id=p_attempt_id;
 IF step.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 canonical:=_canonical_agent_action_batch(step,p_actions);
 batch_hash:=_agent_action_batch_hash(canonical);

 -- Missing hashes are expected from the ModelLoop. Supplied hashes remain
 -- assertions and may not be silently replaced when they disagree.
 IF EXISTS(
  SELECT 1 FROM jsonb_array_elements(p_actions) supplied
  JOIN jsonb_array_elements(canonical) computed
   ON computed->>'action_id'=supplied->>'action_id'
  WHERE (supplied ? 'arguments_hash' AND supplied->>'arguments_hash'
          IS DISTINCT FROM computed->>'arguments_hash')
     OR (supplied ? 'request_hash' AND supplied->>'request_hash'
          IS DISTINCT FROM computed->>'request_hash')
 ) THEN
  RETURN jsonb_build_object('outcome','request_hash_conflict');
 END IF;
 IF EXISTS(
  SELECT 1 FROM jsonb_array_elements(p_actions) supplied
  WHERE supplied ? 'batch_hash'
    AND supplied->>'batch_hash' IS DISTINCT FROM batch_hash
 ) THEN
  RETURN jsonb_build_object('outcome','batch_hash_conflict');
 END IF;

 SELECT jsonb_agg(
   supplied || jsonb_build_object(
    'arguments_hash',computed.item->>'arguments_hash',
    'request_hash',computed.item->>'request_hash',
    'batch_hash',batch_hash)
   ORDER BY (supplied->>'index')::INTEGER,
    btrim(supplied->>'stable_tool_call_id'),
    (supplied->>'action_id')::UUID)
 INTO canonical_actions
 FROM jsonb_array_elements(p_actions) supplied
 CROSS JOIN LATERAL(
  SELECT item FROM jsonb_array_elements(canonical) item
  WHERE item->>'action_id'=supplied->>'action_id'
  ORDER BY (item->>'index')::INTEGER,
   btrim(item->>'stable_tool_call_id'),(item->>'action_id')::UUID
  LIMIT 1
 ) computed;

 result:=complete_model_attempt_step_and_create_actions(
  p_attempt_id,p_run_execution_token,p_expected_attempt_version,
  p_expected_step_version,p_request_hash,p_response_receipt,p_response_hash,
  p_provider_stop_reason,p_usage,p_actual_credits,batch_hash,canonical_actions);
 IF result->>'outcome' NOT IN ('completed','already_completed') THEN
  RETURN result;
 END IF;
 FOR action IN SELECT a.* FROM agent_actions a
   WHERE a.model_step_id=step.id AND a.status='awaiting_authorization'
   ORDER BY a.action_index,a.id
 LOOP
  prompt:=jsonb_build_object(
   'protocol_version',3,'action_id',action.id,
   'tool_call_id',action.stable_tool_call_id,
   'tool_name',action.tool_name,'arguments',action.arguments,
   'arguments_hash',action.arguments_hash);
  opened:=open_agent_authorization_interaction(
   action.id,action.state_version,prompt,
   encode(digest(convert_to(prompt::text,'UTF8'),'sha256'),'hex'),900);
  IF opened->>'outcome' NOT IN ('opened','already_open') THEN
   RAISE EXCEPTION 'AGENT_AUTHORIZATION_INTERACTION_OPEN_FAILED: %',
    opened->>'outcome' USING ERRCODE='55000';
  END IF;
 END LOOP;
 RETURN result;
END $$;

REVOKE ALL ON FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker,
 everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
TO everydayai_agent_runtime_worker;

RESET ROLE;
