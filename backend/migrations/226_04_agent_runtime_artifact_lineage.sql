-- 226_04: Runtime lineage over existing conversation artifacts.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_action_artifact_links (
 action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
 artifact_id UUID NOT NULL REFERENCES conversation_artifacts(id) ON DELETE RESTRICT,
 role TEXT NOT NULL CHECK(role IN ('input','output','partial','materialized')),
 parent_artifact_id UUID REFERENCES conversation_artifacts(id) ON DELETE RESTRICT,
 content_hash TEXT NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'), materialize_revision INTEGER NOT NULL DEFAULT 1 CHECK(materialize_revision>0),
 materialize_status TEXT NOT NULL CHECK(materialize_status IN ('pending','materialized','materialize_failed','partial')),
 sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK(sensitivity IN ('normal','sensitive','restricted')), created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(action_id,attempt_id,artifact_id,role)
);
CREATE UNIQUE INDEX uq_agent_artifact_materialized_hash ON agent_action_artifact_links(action_id,attempt_id,content_hash,materialize_revision,role);
ALTER TABLE agent_action_artifact_links ENABLE ROW LEVEL SECURITY; ALTER TABLE agent_action_artifact_links FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_action_artifact_links_owner_all ON agent_action_artifact_links FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_action_artifact_links FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai_runtime;
CREATE FUNCTION link_agent_action_artifact(UUID,UUID,UUID,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); INSERT INTO agent_action_artifact_links(action_id,attempt_id,artifact_id,role,parent_artifact_id,content_hash,materialize_revision,materialize_status,sensitivity) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING; PERFORM _agent_runtime_226_append_action_event($1,'action.artifact.linked',jsonb_build_object('artifact_id',$3,'role',$4,'materialize_status',$8)); RETURN jsonb_build_object('outcome','linked'); END; $$;
CREATE FUNCTION checkpoint_agent_artifact_materialization(UUID,UUID,UUID,INTEGER,TEXT) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN PERFORM _assert_agent_runtime_actor(TRUE); UPDATE agent_action_artifact_links SET materialize_revision=$4,materialize_status=$5 WHERE action_id=$1 AND attempt_id=$2 AND artifact_id=$3; RETURN jsonb_build_object('outcome','checkpointed'); END; $$;
REVOKE ALL ON FUNCTION link_agent_action_artifact(UUID,UUID,UUID,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),checkpoint_agent_artifact_materialization(UUID,UUID,UUID,INTEGER,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION link_agent_action_artifact(UUID,UUID,UUID,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),checkpoint_agent_artifact_materialization(UUID,UUID,UUID,INTEGER,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
