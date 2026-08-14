-- Roll back only BG3.5 after disposable operation facts are removed.
SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_model_gateway_operations) THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_DISPATCH_BINDING_FACTS_EXIST';
 END IF;
END $$;

REVOKE ALL ON FUNCTION start_agent_runtime_model_gateway_dispatch(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT)
FROM everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION claim_agent_runtime_model_gateway_operation_v2(
 UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER)
FROM everydayai_agent_model_gateway;
DROP FUNCTION claim_agent_runtime_model_gateway_operation_v2(
 UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER);
DROP FUNCTION start_agent_runtime_model_gateway_dispatch(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION _agent_model_gateway_dispatch_fences(UUID,TEXT,TEXT);

GRANT EXECUTE ON FUNCTION submit_agent_runtime_model_gateway_operation(
 UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_model_gateway_operation(
 UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER)
TO everydayai_agent_model_gateway;

RESET ROLE;
