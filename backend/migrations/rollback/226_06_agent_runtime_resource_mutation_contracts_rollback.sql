SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM deleted_files WHERE runtime_action_id IS NOT NULL) OR EXISTS(SELECT 1 FROM scheduled_tasks WHERE runtime_action_id IS NOT NULL) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
DROP FUNCTION runtime_mutate_scheduled_task(UUID,UUID,UUID,BIGINT,TEXT,TEXT,JSONB,UUID); DROP FUNCTION runtime_restore_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT,UUID); DROP FUNCTION runtime_delete_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT,UUID);
DROP INDEX uq_scheduled_tasks_runtime_idempotency,uq_deleted_files_runtime_idempotency;
ALTER TABLE scheduled_tasks DROP COLUMN runtime_idempotency_key, DROP COLUMN runtime_state_version, DROP COLUMN runtime_request_hash, DROP COLUMN runtime_attempt_id, DROP COLUMN runtime_action_id;
ALTER TABLE deleted_files DROP COLUMN runtime_idempotency_key, DROP COLUMN runtime_state_version, DROP COLUMN runtime_request_hash, DROP COLUMN runtime_attempt_id, DROP COLUMN runtime_action_id;
RESET ROLE;
