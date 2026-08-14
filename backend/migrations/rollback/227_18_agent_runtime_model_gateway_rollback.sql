SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_model_gateway_operations) THEN
  RAISE EXCEPTION 'AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST';
 END IF;
END $$;
REVOKE ALL ON FUNCTION submit_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT) FROM everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION claim_agent_runtime_model_gateway_operation(UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT),
 mark_agent_runtime_model_gateway_dispatched(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT),
 renew_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER),
 finalize_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,BOOLEAN,TEXT,JSONB,TEXT,TEXT),
 recover_agent_runtime_model_gateway_operations(TEXT,INTEGER,INTEGER) FROM everydayai_agent_model_gateway;
DROP FUNCTION recover_agent_runtime_model_gateway_operations(TEXT,INTEGER,INTEGER);
DROP FUNCTION finalize_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,BOOLEAN,TEXT,JSONB,TEXT,TEXT);
DROP FUNCTION renew_agent_runtime_model_gateway_operation(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER);
DROP FUNCTION mark_agent_runtime_model_gateway_dispatched(UUID,UUID,BIGINT,UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT);
DROP FUNCTION claim_agent_runtime_model_gateway_operation(UUID,TEXT,TEXT,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT,INTEGER);
DROP FUNCTION read_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,TEXT);
DROP FUNCTION submit_agent_runtime_model_gateway_operation(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,BIGINT,BIGINT);
DROP FUNCTION _agent_model_gateway_fences(UUID,TEXT,TEXT,BIGINT,BIGINT,BIGINT,TEXT);
DROP FUNCTION _agent_model_gateway_public(agent_runtime_model_gateway_operations);
DROP FUNCTION _assert_agent_model_gateway_actor(TEXT);
DROP TABLE agent_runtime_model_gateway_operations;
REVOKE USAGE ON SCHEMA public FROM everydayai_agent_model_gateway;
-- Runtime's former broad bundle grant is intentionally not restored.
RESET ROLE;
