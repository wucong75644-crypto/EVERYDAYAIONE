-- Roll back 227_35 only while no frozen delivery facts exist.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_delivery_intents,
 agent_runtime_scheduled_delivery_runtime_bindings,
 agent_runtime_scheduled_delivery_targets,
 agent_runtime_scheduled_delivery_snapshots IN SHARE ROW EXCLUSIVE MODE;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_intents)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_runtime_bindings)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_targets)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_snapshots) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_ROLLBACK_FACTS_EXIST'
   USING ERRCODE='55000';
 END IF;
END $$;

REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;

DROP TRIGGER capture_runtime_scheduled_delivery_intents
 ON agent_runtime_scheduled_finalization_intents;
DROP TRIGGER bind_runtime_scheduled_delivery_runtime_run
 ON agent_runtime_scheduled_run_bindings;
DROP TRIGGER capture_runtime_scheduled_delivery_snapshot
 ON agent_runtime_scheduled_submission_intents;
DROP FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID);
DROP FUNCTION _capture_agent_runtime_scheduled_delivery_intents();
DROP FUNCTION _bind_agent_runtime_scheduled_delivery_runtime_run();
DROP FUNCTION _capture_agent_runtime_scheduled_delivery_snapshot();
DROP FUNCTION _agent_runtime_scheduled_delivery_normalize(UUID,UUID,JSONB,INTEGER);
DROP TABLE agent_runtime_scheduled_delivery_intents;
DROP TABLE agent_runtime_scheduled_delivery_runtime_bindings;
DROP TABLE agent_runtime_scheduled_delivery_targets;
DROP TABLE agent_runtime_scheduled_delivery_snapshots;

RESET ROLE;
