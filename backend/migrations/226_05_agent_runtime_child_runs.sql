-- 226_05: explicit parent/child Runtime identity for composite Executors.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_runs
 ADD COLUMN parent_run_id UUID REFERENCES agent_runs(id) ON DELETE RESTRICT,
 ADD COLUMN parent_action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,
 ADD COLUMN child_ordinal INTEGER CHECK(child_ordinal IS NULL OR child_ordinal>=0),
 ADD COLUMN parent_request_hash TEXT CHECK(parent_request_hash IS NULL OR parent_request_hash ~ '^[0-9a-f]{64}$'),
 ADD COLUMN aggregation_revision INTEGER NOT NULL DEFAULT 0 CHECK(aggregation_revision>=0);
ALTER TABLE agent_runs ADD CONSTRAINT agent_run_child_pair CHECK((parent_run_id IS NULL)=(parent_action_id IS NULL));
CREATE UNIQUE INDEX uq_agent_child_ordinal ON agent_runs(parent_action_id,child_ordinal) WHERE parent_action_id IS NOT NULL;
CREATE INDEX idx_agent_child_parent ON agent_runs(parent_run_id) WHERE parent_run_id IS NOT NULL;

CREATE FUNCTION create_agent_child_run(UUID,UUID,TEXT,INTEGER,TEXT,JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE p agent_runs%ROWTYPE; a agent_actions%ROWTYPE; c agent_runs%ROWTYPE; child_command UUID;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE); SELECT * INTO a FROM agent_actions WHERE id=$2; SELECT * INTO p FROM agent_runs WHERE id=$1;
 IF p.id IS NULL OR a.id IS NULL OR a.run_id<>p.id OR $3 IS NULL OR $4<0 OR $5 IS NULL THEN RAISE EXCEPTION 'AGENT_CHILD_RUN_BINDING_INVALID'; END IF;
 INSERT INTO agent_session_commands(session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash)
 VALUES(p.session_id,p.org_id,p.user_id,'submit_input','child:'||a.id::TEXT||':'||$4,
        jsonb_build_object('parent_run_id',p.id,'parent_action_id',a.id,'request_hash',$5),md5($5))
 RETURNING id INTO child_command;
 INSERT INTO agent_runs(session_id,command_id,org_id,user_id,run_kind,status,idempotency_key,request_hash,context_receipt,config_snapshot,capability_snapshot,parent_run_id,parent_action_id,child_ordinal,parent_request_hash)
 VALUES(p.session_id,child_command,p.org_id,p.user_id,'continuation','queued','child:'||a.id::TEXT||':'||$4,md5($5),COALESCE($6,'{}'),p.config_snapshot,p.capability_snapshot,p.id,a.id,$4,$5) RETURNING * INTO c;
 PERFORM _agent_runtime_226_append_action_event(a.id,'action.child_run.created',jsonb_build_object('child_run_id',c.id,'child_ordinal',$4));
 RETURN jsonb_build_object('outcome','created','child_run_id',c.id,'parent_run_id',p.id,'parent_action_id',a.id);
END; $$;
CREATE FUNCTION complete_agent_child_run(UUID,UUID,INTEGER,JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE c agent_runs%ROWTYPE;
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); UPDATE agent_runs SET status='completed',result_hash=md5(COALESCE($4,'{}')::TEXT),aggregation_revision=$3,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=$1 AND parent_run_id=$2 AND status NOT IN ('completed','failed','cancelled') RETURNING * INTO c; IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF; RETURN jsonb_build_object('outcome','completed','child_run_id',c.id); END; $$;
CREATE FUNCTION cancel_agent_child_run(UUID,UUID,TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); UPDATE agent_runs SET status='cancelled',terminal_reason=$3,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=$1 AND parent_run_id=$2 AND status NOT IN ('completed','failed','cancelled'); RETURN jsonb_build_object('outcome','cancelled'); END; $$;
ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY; ALTER TABLE agent_runs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON FUNCTION create_agent_child_run(UUID,UUID,TEXT,INTEGER,TEXT,JSONB),complete_agent_child_run(UUID,UUID,INTEGER,JSONB),cancel_agent_child_run(UUID,UUID,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_agent_child_run(UUID,UUID,TEXT,INTEGER,TEXT,JSONB),complete_agent_child_run(UUID,UUID,INTEGER,JSONB),cancel_agent_child_run(UUID,UUID,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
