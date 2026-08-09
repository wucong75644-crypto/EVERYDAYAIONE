SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_finalization_intents)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_run_bindings b JOIN agent_runs r
  ON r.id=b.runtime_run_id WHERE b.owner_kind='runtime' AND r.run_kind='scheduled'
  AND r.status IN('completed','failed','cancelled')) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_FINALIZATION_ROLLBACK_FACTS_EXIST' USING ERRCODE='55000';
 END IF;
END $$;
REVOKE ALL ON FUNCTION claim_next_agent_runtime_scheduled_finalization_v1(TEXT,INTEGER),
 read_agent_runtime_scheduled_finalization_v1(UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker;
DROP TRIGGER capture_runtime_scheduled_terminal_intent ON agent_runs;
DROP FUNCTION _capture_agent_runtime_scheduled_terminal_intent();
DROP FUNCTION claim_next_agent_runtime_scheduled_finalization_v1(TEXT,INTEGER);
DROP FUNCTION read_agent_runtime_scheduled_finalization_v1(UUID,UUID);
DROP FUNCTION _agent_runtime_scheduled_finalization_payload(agent_runtime_scheduled_finalization_intents);
DROP TRIGGER runtime_scheduled_finalization_immutable ON agent_runtime_scheduled_finalization_intents;
DROP FUNCTION _agent_runtime_scheduled_finalization_immutable();
DROP FUNCTION _agent_runtime_scheduled_terminal_reason(TEXT,TEXT);
DROP TABLE agent_runtime_scheduled_finalization_intents;
RESET ROLE;
