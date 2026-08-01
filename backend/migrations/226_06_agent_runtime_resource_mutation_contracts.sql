-- 226_06: Runtime bindings for resource and scheduled-task mutations.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE deleted_files ADD COLUMN runtime_action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,
 ADD COLUMN runtime_attempt_id UUID REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
 ADD COLUMN runtime_request_hash TEXT, ADD COLUMN runtime_state_version BIGINT NOT NULL DEFAULT 0,
 ADD COLUMN runtime_idempotency_key TEXT;
ALTER TABLE scheduled_tasks ADD COLUMN runtime_action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,
 ADD COLUMN runtime_attempt_id UUID REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
 ADD COLUMN runtime_request_hash TEXT, ADD COLUMN runtime_state_version BIGINT NOT NULL DEFAULT 0,
 ADD COLUMN runtime_idempotency_key TEXT;
CREATE UNIQUE INDEX uq_deleted_files_runtime_idempotency ON deleted_files(runtime_idempotency_key) WHERE runtime_idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX uq_scheduled_tasks_runtime_idempotency ON scheduled_tasks(runtime_idempotency_key) WHERE runtime_idempotency_key IS NOT NULL;

CREATE FUNCTION runtime_delete_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d deleted_files%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); SELECT * INTO d FROM deleted_files WHERE id=$1 FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'RUNTIME_RESOURCE_NOT_FOUND'; END IF; IF d.runtime_action_id IS NOT NULL AND d.runtime_action_id<>$2 THEN RAISE EXCEPTION 'RUNTIME_RESOURCE_FENCED'; END IF; UPDATE deleted_files SET runtime_action_id=$2,runtime_attempt_id=$3,runtime_request_hash=$4,runtime_idempotency_key=$5,runtime_state_version=runtime_state_version+1 WHERE id=$1 RETURNING * INTO d; PERFORM _agent_runtime_226_append_action_event($2,'action.workspace.delete.bound',jsonb_build_object('deleted_file_id',d.id)); RETURN jsonb_build_object('outcome','bound','deleted_file_id',d.id,'state_version',d.runtime_state_version); END; $$;
CREATE FUNCTION runtime_restore_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE d deleted_files%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); SELECT * INTO d FROM deleted_files WHERE id=$1 FOR UPDATE; IF NOT FOUND OR d.purged THEN RETURN jsonb_build_object('outcome','not_found'); END IF; IF d.runtime_action_id IS NOT NULL AND d.runtime_action_id<>$2 THEN RAISE EXCEPTION 'RUNTIME_RESOURCE_FENCED'; END IF; UPDATE deleted_files SET runtime_action_id=$2,runtime_attempt_id=$3,runtime_request_hash=$4,runtime_idempotency_key=$5,runtime_state_version=runtime_state_version+1 WHERE id=$1 RETURNING * INTO d; PERFORM _agent_runtime_226_append_action_event($2,'action.workspace.restore.bound',jsonb_build_object('deleted_file_id',d.id)); RETURN jsonb_build_object('outcome','bound','deleted_file_id',d.id,'state_version',d.runtime_state_version); END; $$;
CREATE FUNCTION runtime_mutate_scheduled_task(UUID,UUID,UUID,BIGINT,TEXT,TEXT,JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); SELECT * INTO t FROM scheduled_tasks WHERE id=$1 FOR UPDATE; IF NOT FOUND OR t.runtime_state_version<>$4 THEN RETURN jsonb_build_object('outcome','cas_conflict'); END IF; UPDATE scheduled_tasks SET runtime_action_id=$2,runtime_attempt_id=$3,runtime_request_hash=$5,runtime_idempotency_key=$6,runtime_state_version=runtime_state_version+1,updated_at=clock_timestamp() WHERE id=$1 RETURNING * INTO t; RETURN jsonb_build_object('outcome','updated','task_id',t.id,'state_version',t.runtime_state_version); END; $$;
REVOKE ALL ON TABLE deleted_files,scheduled_tasks FROM everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION runtime_delete_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT),runtime_restore_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT),runtime_mutate_scheduled_task(UUID,UUID,UUID,BIGINT,TEXT,TEXT,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION runtime_delete_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT),runtime_restore_workspace_resource(BIGINT,UUID,UUID,TEXT,TEXT),runtime_mutate_scheduled_task(UUID,UUID,UUID,BIGINT,TEXT,TEXT,JSONB) TO everydayai_agent_runtime_worker;
RESET ROLE;
