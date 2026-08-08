-- Remove only the additive BG2.1 RPC. Existing operation facts remain readable.
SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_model_gateway_operations) THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST';
 END IF;
END $$;

REVOKE ALL ON FUNCTION fail_agent_runtime_model_gateway_claim(
 UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_agent_model_gateway,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
DROP FUNCTION fail_agent_runtime_model_gateway_claim(
 UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT);

RESET ROLE;
