-- 227_35: Frozen Scheduled Runtime delivery targets and pending delivery intents.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_submission_intents,
 agent_runtime_scheduled_run_bindings,
 agent_runtime_scheduled_finalization_intents IN SHARE ROW EXCLUSIVE MODE;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_submission_intents)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_run_bindings WHERE owner_kind='runtime')
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_finalization_intents) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_BACKFILL_REQUIRED'
   USING ERRCODE='55000';
 END IF;
END $$;

CREATE TABLE agent_runtime_scheduled_delivery_snapshots(
 scheduled_run_id UUID PRIMARY KEY REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 runtime_command_id UUID NOT NULL UNIQUE REFERENCES agent_session_commands(id) ON DELETE RESTRICT,
 profile_state_version BIGINT NOT NULL CHECK(profile_state_version>0),
 task_revision BIGINT NOT NULL CHECK(task_revision>=0),
 target_set_hash TEXT NOT NULL CHECK(target_set_hash~'^[0-9a-f]{64}$'),
 target_count INTEGER NOT NULL CHECK(target_count BETWEEN 1 AND 20),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE agent_runtime_scheduled_delivery_targets(
 scheduled_run_id UUID NOT NULL REFERENCES agent_runtime_scheduled_delivery_snapshots(scheduled_run_id)
  ON DELETE RESTRICT,
 target_key TEXT NOT NULL CHECK(length(target_key) BETWEEN 3 AND 240),
 target_hash TEXT NOT NULL CHECK(target_hash~'^[0-9a-f]{64}$'),
 target_type TEXT NOT NULL CHECK(target_type IN('web','wecom_user','wecom_group')),
 target_snapshot JSONB NOT NULL CHECK(jsonb_typeof(target_snapshot)='object'),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 20),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scheduled_run_id,target_key),
 UNIQUE(scheduled_run_id,target_key,target_hash),
 UNIQUE(scheduled_run_id,ordinal)
);
CREATE TABLE agent_runtime_scheduled_delivery_runtime_bindings(
 scheduled_run_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_delivery_snapshots(scheduled_run_id)
  ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE agent_runtime_scheduled_delivery_contents(
 scheduled_run_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_delivery_snapshots(scheduled_run_id)
  ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
 model_result_id UUID REFERENCES agent_model_results(id) ON DELETE RESTRICT,
 terminal_status TEXT NOT NULL CHECK(terminal_status IN('completed','failed','cancelled')),
 result_hash TEXT CHECK(result_hash IS NULL OR result_hash~'^[0-9a-f]{64}$'),
 reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 80),
 artifact_manifest JSONB NOT NULL CHECK(jsonb_typeof(artifact_manifest)='array'),
 artifact_manifest_hash TEXT NOT NULL CHECK(artifact_manifest_hash~'^[0-9a-f]{64}$'),
 content_identity_hash TEXT NOT NULL UNIQUE CHECK(content_identity_hash~'^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(scheduled_run_id,content_identity_hash),
 CHECK((terminal_status='completed')=(model_result_id IS NOT NULL)),
 CHECK((terminal_status='completed')=(result_hash IS NOT NULL)),
 CHECK((terminal_status='completed')=(reason_code IS NULL)),
 CHECK(terminal_status='completed' OR artifact_manifest='[]'::JSONB)
);
CREATE TABLE agent_runtime_scheduled_delivery_intents(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 scheduled_run_id UUID NOT NULL,
 target_key TEXT NOT NULL,
 target_hash TEXT NOT NULL CHECK(target_hash~'^[0-9a-f]{64}$'),
 runtime_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 terminal_status TEXT NOT NULL CHECK(terminal_status IN('completed','failed','cancelled')),
 result_hash TEXT CHECK(result_hash IS NULL OR result_hash~'^[0-9a-f]{64}$'),
 reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 80),
 content_identity_hash TEXT NOT NULL CHECK(content_identity_hash~'^[0-9a-f]{64}$'),
 finalization_request_id UUID NOT NULL,
 finalization_application_hash TEXT NOT NULL CHECK(finalization_application_hash~'^[0-9a-f]{64}$'),
 idempotency_key TEXT NOT NULL UNIQUE CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status='pending'),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(scheduled_run_id,target_key),
 FOREIGN KEY(scheduled_run_id,target_key,target_hash)
  REFERENCES agent_runtime_scheduled_delivery_targets(scheduled_run_id,target_key,target_hash)
  ON DELETE RESTRICT,
 FOREIGN KEY(scheduled_run_id,content_identity_hash)
  REFERENCES agent_runtime_scheduled_delivery_contents(scheduled_run_id,content_identity_hash)
  ON DELETE RESTRICT,
 CHECK((terminal_status='completed')=(result_hash IS NOT NULL)),
 CHECK((terminal_status='completed')=(reason_code IS NULL))
);
CREATE INDEX idx_runtime_scheduled_delivery_intents_pending
 ON agent_runtime_scheduled_delivery_intents(created_at,scheduled_run_id,target_key)
 WHERE status='pending';

ALTER TABLE agent_runtime_scheduled_delivery_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_targets FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_runtime_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_runtime_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_contents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_contents FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_delivery_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_delivery_snapshots_owner
 ON agent_runtime_scheduled_delivery_snapshots FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_delivery_targets_owner
 ON agent_runtime_scheduled_delivery_targets FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_delivery_bindings_owner
 ON agent_runtime_scheduled_delivery_runtime_bindings FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_delivery_contents_owner
 ON agent_runtime_scheduled_delivery_contents FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_delivery_intents_owner
 ON agent_runtime_scheduled_delivery_intents FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_delivery_snapshots,
 agent_runtime_scheduled_delivery_targets,
 agent_runtime_scheduled_delivery_runtime_bindings,
 agent_runtime_scheduled_delivery_contents,
 agent_runtime_scheduled_delivery_intents
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE TRIGGER runtime_scheduled_delivery_snapshot_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_snapshots FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_delivery_target_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_targets FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_delivery_binding_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_runtime_bindings FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_delivery_content_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_contents FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_delivery_intent_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_intents FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();

CREATE FUNCTION _agent_runtime_scheduled_delivery_normalize(
 p_org_id UUID,p_user_id UUID,p_target JSONB,p_depth INTEGER DEFAULT 0)
RETURNS TABLE(target_key TEXT,target_hash TEXT,target_type TEXT,target_snapshot JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE kind TEXT;identifier TEXT;item JSONB;canonical JSONB;matched INTEGER;
 identity_id UUID;identity_corp TEXT;identity_user UUID;identity_channel TEXT;identity_chat_type TEXT;
BEGIN
 IF p_depth NOT BETWEEN 0 AND 1 OR jsonb_typeof(p_target)<>'object' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_DENIED' USING ERRCODE='42501';
 END IF;
 kind:=p_target->>'type';
 IF kind='multi' THEN
  IF p_depth<>0 OR NOT _runtime_scheduler_actor_allowed(p_org_id,p_user_id,TRUE)
  OR jsonb_typeof(p_target->'targets')<>'array'
  OR jsonb_array_length(p_target->'targets') NOT BETWEEN 1 AND 20 THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_DENIED' USING ERRCODE='42501';
  END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_target->'targets') LOOP
   IF jsonb_typeof(item)<>'object' OR item->>'type' NOT IN('web','wecom_user','wecom_group') THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_NESTED_MULTI_DENIED' USING ERRCODE='22023';
   END IF;
   RETURN QUERY SELECT * FROM _agent_runtime_scheduled_delivery_normalize(
    p_org_id,p_user_id,item,1);
  END LOOP;
  RETURN;
 END IF;
 IF NOT _runtime_scheduler_push_target_allowed(p_org_id,p_user_id,p_target,p_depth) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_DENIED' USING ERRCODE='42501';
 ELSIF kind='web' THEN
  BEGIN identifier:=(p_target->>'user_id')::UUID::TEXT;
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
  END;
  canonical:=jsonb_build_object('type','web','org_id',p_org_id,'user_id',identifier);
 ELSIF kind='wecom_user' THEN
  identifier:=btrim(p_target->>'wecom_userid');
  SELECT count(*),(array_agg(m.id))[1],(array_agg(m.corp_id))[1],
   (array_agg(m.user_id))[1],(array_agg(m.channel))[1] INTO matched,
   identity_id,identity_corp,identity_user,identity_channel
  FROM wecom_user_mappings m JOIN org_members member
   ON member.org_id=m.org_id AND member.user_id=m.user_id AND member.status='active'
  WHERE m.org_id=p_org_id AND m.wecom_userid=identifier;
  IF matched<>1 THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_IDENTITY_CONFLICT'
   USING ERRCODE='55000'; END IF;
  canonical:=jsonb_build_object('type','wecom_user','mapping_id',identity_id,
   'corp_id',identity_corp,'mapping_user_id',identity_user,'org_id',p_org_id,
   'wecom_userid',identifier,'channel',identity_channel);
 ELSIF kind='wecom_group' THEN
  identifier:=btrim(p_target->>'chatid');
  SELECT count(*),(array_agg(g.id))[1],(array_agg(g.corp_id))[1],
   (array_agg(g.chat_type))[1] INTO matched,
   identity_id,identity_corp,identity_chat_type FROM wecom_chat_targets g
  WHERE g.org_id=p_org_id AND g.chatid=identifier AND g.is_active;
  IF matched<>1 THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_IDENTITY_CONFLICT'
   USING ERRCODE='55000'; END IF;
  canonical:=jsonb_build_object('type','wecom_group','target_id',identity_id,
   'corp_id',identity_corp,'org_id',p_org_id,'chatid',identifier,
   'chat_type',identity_chat_type);
 ELSE
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
 END IF;
 IF identifier IS NULL OR length(identifier) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
 END IF;
 target_key:=kind||':'||CASE WHEN kind IN('wecom_user','wecom_group')
  THEN identity_id::TEXT ELSE identifier END;
 target_hash:=encode(digest(convert_to(
  _agent_runtime_scheduled_canonical_json(canonical),'UTF8'),'sha256'),'hex');
 target_type:=kind;target_snapshot:=canonical;
 RETURN NEXT;
END $$;

CREATE FUNCTION _capture_agent_runtime_scheduled_delivery_snapshot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE task scheduled_tasks%ROWTYPE;binding agent_runtime_scheduled_run_bindings%ROWTYPE;
 profile agent_runtime_scheduled_execution_profiles%ROWTYPE;raw_count INTEGER;targets JSONB;
 unique_count INTEGER;target_set_hash TEXT;item JSONB;position INTEGER:=0;
BEGIN
 SELECT * INTO task FROM scheduled_tasks WHERE id=NEW.scheduled_task_id FOR SHARE;
 SELECT * INTO binding FROM agent_runtime_scheduled_run_bindings
  WHERE scheduled_run_id=NEW.scheduled_run_id FOR SHARE;
 SELECT * INTO profile FROM agent_runtime_scheduled_execution_profiles
  WHERE scheduled_task_id=NEW.scheduled_task_id;
 IF task.id IS NULL OR binding.scheduled_run_id IS NULL OR profile.scheduled_task_id IS NULL
 OR binding.owner_kind<>'runtime' OR binding.runtime_run_id IS NOT NULL
 OR(binding.scheduled_task_id,binding.org_id,binding.user_id,binding.runtime_command_id,
    binding.task_revision,binding.profile_state_version)
  IS DISTINCT FROM(NEW.scheduled_task_id,NEW.org_id,NEW.user_id,NEW.command_id,
    NEW.task_revision,NEW.profile_state_version)
 OR(task.org_id,task.user_id,task.runtime_state_version)
  IS DISTINCT FROM(NEW.org_id,NEW.user_id,NEW.task_revision)
 OR(profile.org_id,profile.user_id,profile.state_version)
  IS DISTINCT FROM(NEW.org_id,NEW.user_id,NEW.profile_state_version) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_SNAPSHOT_FENCED' USING ERRCODE='55000';
 END IF;
 SELECT count(*),count(DISTINCT normalized.target_key),
  jsonb_agg(to_jsonb(normalized) ORDER BY normalized.target_key)
 INTO raw_count,unique_count,targets
 FROM _agent_runtime_scheduled_delivery_normalize(
  NEW.org_id,NEW.user_id,task.push_target,0) normalized;
 IF raw_count NOT BETWEEN 1 AND 20 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_COUNT_INVALID' USING ERRCODE='22023';
 END IF;
 IF unique_count<>raw_count THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_DUPLICATE' USING ERRCODE='22023';
 END IF;
 target_set_hash:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(
  jsonb_build_object('targets',targets)),'UTF8'),'sha256'),'hex');
 INSERT INTO agent_runtime_scheduled_delivery_snapshots(
  scheduled_run_id,scheduled_task_id,org_id,user_id,runtime_command_id,
  profile_state_version,task_revision,target_set_hash,target_count)
 VALUES(NEW.scheduled_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,NEW.command_id,
  NEW.profile_state_version,NEW.task_revision,target_set_hash,jsonb_array_length(targets));
 FOR item IN SELECT value FROM jsonb_array_elements(targets) LOOP
  position:=position+1;
  INSERT INTO agent_runtime_scheduled_delivery_targets(
   scheduled_run_id,target_key,target_hash,target_type,target_snapshot,ordinal)
  VALUES(NEW.scheduled_run_id,item->>'target_key',item->>'target_hash',
   item->>'target_type',item->'target_snapshot',position);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER capture_runtime_scheduled_delivery_snapshot
 AFTER INSERT ON agent_runtime_scheduled_submission_intents FOR EACH ROW
 EXECUTE FUNCTION _capture_agent_runtime_scheduled_delivery_snapshot();

CREATE FUNCTION _bind_agent_runtime_scheduled_delivery_runtime_run() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE snapshot agent_runtime_scheduled_delivery_snapshots%ROWTYPE;run agent_runs%ROWTYPE;
BEGIN
 IF NEW.runtime_run_id IS NULL OR OLD.runtime_run_id IS NOT NULL THEN RETURN NEW; END IF;
 SELECT * INTO snapshot FROM agent_runtime_scheduled_delivery_snapshots
  WHERE scheduled_run_id=NEW.scheduled_run_id FOR SHARE;
 SELECT * INTO run FROM agent_runs WHERE id=NEW.runtime_run_id FOR SHARE;
 IF snapshot.scheduled_run_id IS NULL OR NEW.owner_kind<>'runtime'
 OR(snapshot.scheduled_task_id,snapshot.org_id,snapshot.user_id,snapshot.runtime_command_id,
    snapshot.profile_state_version,snapshot.task_revision)
  IS DISTINCT FROM(NEW.scheduled_task_id,NEW.org_id,NEW.user_id,NEW.runtime_command_id,
    NEW.profile_state_version,NEW.task_revision)
 OR(run.command_id,run.org_id,run.user_id,run.run_kind)
  IS DISTINCT FROM(NEW.runtime_command_id,NEW.org_id,NEW.user_id,'scheduled') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_RUNTIME_BINDING_FENCED' USING ERRCODE='55000';
 END IF;
 INSERT INTO agent_runtime_scheduled_delivery_runtime_bindings(scheduled_run_id,runtime_run_id)
 VALUES(NEW.scheduled_run_id,NEW.runtime_run_id);
 RETURN NEW;
END $$;
CREATE TRIGGER bind_runtime_scheduled_delivery_runtime_run
 AFTER UPDATE OF runtime_run_id ON agent_runtime_scheduled_run_bindings FOR EACH ROW
 EXECUTE FUNCTION _bind_agent_runtime_scheduled_delivery_runtime_run();

CREATE FUNCTION _capture_agent_runtime_scheduled_delivery_intents() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE snapshot agent_runtime_scheduled_delivery_snapshots%ROWTYPE;
 runtime_binding agent_runtime_scheduled_delivery_runtime_bindings%ROWTYPE;
 target agent_runtime_scheduled_delivery_targets%ROWTYPE;content_hash TEXT;intent_key TEXT;
 prior agent_runtime_scheduled_delivery_intents%ROWTYPE;content agent_runtime_scheduled_delivery_contents%ROWTYPE;
 reason TEXT;manifest JSONB:='[]'::JSONB;manifest_hash TEXT;model_result UUID;model_hash TEXT;
BEGIN
 IF NEW.status<>'applied' OR OLD.status='applied' THEN RETURN NEW; END IF;
 SELECT * INTO snapshot FROM agent_runtime_scheduled_delivery_snapshots
  WHERE scheduled_run_id=NEW.scheduled_run_id FOR SHARE;
 SELECT * INTO runtime_binding FROM agent_runtime_scheduled_delivery_runtime_bindings
  WHERE scheduled_run_id=NEW.scheduled_run_id FOR SHARE;
 IF snapshot.scheduled_run_id IS NULL OR runtime_binding.scheduled_run_id IS NULL
 OR(runtime_binding.runtime_run_id,snapshot.scheduled_task_id,snapshot.org_id,snapshot.user_id)
  IS DISTINCT FROM(NEW.runtime_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id)
 OR NEW.application_request_id IS NULL OR NEW.application_hash !~ '^[0-9a-f]{64}$'
 OR NEW.application_receipt->>'terminal_status' IS DISTINCT FROM NEW.terminal_status
 OR NEW.application_receipt->>'result_hash' IS DISTINCT FROM NEW.result_hash THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_FINALIZATION_FENCED' USING ERRCODE='55000';
 END IF;
 reason:=CASE WHEN NEW.terminal_status='completed' THEN NULL ELSE NEW.terminal_reason END;
 IF NEW.terminal_status='completed' THEN
  SELECT mr.id,mr.content_hash INTO model_result,model_hash FROM agent_runs ar
  JOIN agent_model_steps ms ON ms.run_id=ar.id JOIN agent_model_results mr
   ON mr.model_step_id=ms.id WHERE ar.id=NEW.runtime_run_id AND mr.run_id=ar.id
   AND mr.org_id IS NOT DISTINCT FROM NEW.org_id AND mr.user_id IS NOT DISTINCT FROM NEW.user_id
   AND ms.session_id=ar.session_id AND mr.session_id=ar.session_id
   AND ms.status='completed' AND ms.stop_reason IN('final','structured_final')
   ORDER BY ms.step_number DESC LIMIT 1;
  IF model_result IS NULL OR model_hash IS DISTINCT FROM NEW.result_hash
  OR EXISTS(SELECT 1 FROM agent_action_artifact_links l
   JOIN agent_actions a ON a.id=l.action_id
   LEFT JOIN agent_action_attempts aa ON aa.id=l.attempt_id AND aa.action_id=a.id
   JOIN conversation_artifacts ca ON ca.id=l.artifact_id
   JOIN agent_runtime_sessions ars ON ars.id=(SELECT session_id FROM agent_runs WHERE id=NEW.runtime_run_id)
   WHERE a.run_id=NEW.runtime_run_id AND(aa.id IS NULL OR aa.run_id<>NEW.runtime_run_id
    OR a.session_id<>ars.id OR aa.session_id<>ars.id OR a.org_id IS DISTINCT FROM NEW.org_id
    OR aa.org_id IS DISTINCT FROM NEW.org_id OR a.user_id IS DISTINCT FROM NEW.user_id
    OR aa.user_id IS DISTINCT FROM NEW.user_id OR ca.org_id IS DISTINCT FROM NEW.org_id
    OR ca.conversation_id<>ars.conversation_id OR ca.content_hash IS DISTINCT FROM l.content_hash
    OR l.content_hash!~'^[0-9a-f]{64}$')) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_CONTENT_FENCED' USING ERRCODE='55000';
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object('artifact_id',l.artifact_id,
   'content_hash',l.content_hash,'role',l.role,'materialize_revision',l.materialize_revision,
   'materialize_status',l.materialize_status) ORDER BY a.action_index,l.created_at,l.artifact_id,l.role),
   '[]'::JSONB) INTO manifest FROM agent_action_artifact_links l
  JOIN agent_actions a ON a.id=l.action_id JOIN agent_action_attempts aa
   ON aa.id=l.attempt_id AND aa.action_id=a.id JOIN conversation_artifacts ca ON ca.id=l.artifact_id
  JOIN agent_runtime_sessions ars ON ars.id=a.session_id WHERE a.run_id=NEW.runtime_run_id
   AND aa.run_id=NEW.runtime_run_id AND a.org_id IS NOT DISTINCT FROM NEW.org_id
   AND aa.org_id IS NOT DISTINCT FROM NEW.org_id AND ca.org_id IS NOT DISTINCT FROM NEW.org_id
   AND ca.conversation_id=ars.conversation_id AND ca.content_hash=l.content_hash;
 END IF;
 manifest_hash:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(manifest),'UTF8'),'sha256'),'hex');
 content_hash:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
  'scheduled_run_id',NEW.scheduled_run_id,'runtime_run_id',NEW.runtime_run_id,
  'terminal_status',NEW.terminal_status,'model_result_id',model_result,'result_hash',NEW.result_hash,
  'reason_code',reason,'artifact_manifest_hash',manifest_hash)),'UTF8'),'sha256'),'hex');
 INSERT INTO agent_runtime_scheduled_delivery_contents(scheduled_run_id,runtime_run_id,
  model_result_id,terminal_status,result_hash,reason_code,artifact_manifest,
  artifact_manifest_hash,content_identity_hash) VALUES(NEW.scheduled_run_id,NEW.runtime_run_id,
  model_result,NEW.terminal_status,NEW.result_hash,reason,manifest,manifest_hash,content_hash)
 ON CONFLICT(scheduled_run_id) DO NOTHING RETURNING * INTO content;
 IF content.scheduled_run_id IS NULL THEN SELECT * INTO content
  FROM agent_runtime_scheduled_delivery_contents WHERE scheduled_run_id=NEW.scheduled_run_id; END IF;
 IF(content.runtime_run_id,content.model_result_id,content.terminal_status,content.result_hash,
    content.reason_code,content.artifact_manifest,content.artifact_manifest_hash,content.content_identity_hash)
  IS DISTINCT FROM(NEW.runtime_run_id,model_result,NEW.terminal_status,NEW.result_hash,
    reason,manifest,manifest_hash,content_hash) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_CONTENT_CONFLICT' USING ERRCODE='55000';
 END IF;
 FOR target IN SELECT * FROM agent_runtime_scheduled_delivery_targets
  WHERE scheduled_run_id=NEW.scheduled_run_id ORDER BY target_key LOOP
 intent_key:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
   'scheduled_run_id',NEW.scheduled_run_id,'runtime_run_id',NEW.runtime_run_id,
   'target_key',target.target_key,'target_hash',target.target_hash,
   'terminal_status',NEW.terminal_status,
   'content_identity_hash',content_hash)),'UTF8'),'sha256'),'hex');
  prior:=NULL;
  INSERT INTO agent_runtime_scheduled_delivery_intents(
   scheduled_run_id,target_key,target_hash,runtime_run_id,scheduled_task_id,org_id,user_id,
   terminal_status,result_hash,reason_code,content_identity_hash,
   finalization_request_id,finalization_application_hash,idempotency_key)
  VALUES(NEW.scheduled_run_id,target.target_key,target.target_hash,NEW.runtime_run_id,NEW.scheduled_task_id,
   NEW.org_id,NEW.user_id,NEW.terminal_status,NEW.result_hash,reason,content_hash,
   NEW.application_request_id,NEW.application_hash,intent_key)
  ON CONFLICT(scheduled_run_id,target_key) DO NOTHING RETURNING * INTO prior;
  IF prior.id IS NULL THEN
   SELECT * INTO prior FROM agent_runtime_scheduled_delivery_intents
    WHERE scheduled_run_id=NEW.scheduled_run_id AND target_key=target.target_key;
  END IF;
  IF(prior.target_hash,prior.runtime_run_id,prior.scheduled_task_id,prior.org_id,prior.user_id,
     prior.terminal_status,prior.result_hash,prior.reason_code,prior.content_identity_hash,
     prior.finalization_request_id,prior.finalization_application_hash,prior.idempotency_key)
   IS DISTINCT FROM(target.target_hash,NEW.runtime_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,
     NEW.terminal_status,NEW.result_hash,reason,content_hash,NEW.application_request_id,
     NEW.application_hash,intent_key) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_INTENT_CONFLICT' USING ERRCODE='55000';
  END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER capture_runtime_scheduled_delivery_intents
 AFTER UPDATE OF status ON agent_runtime_scheduled_finalization_intents FOR EACH ROW
 EXECUTE FUNCTION _capture_agent_runtime_scheduled_delivery_intents();

CREATE FUNCTION _agent_runtime_scheduled_delivery_target_available(
 p_org_id UUID,p_target JSONB) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT CASE p_target->>'type'
 WHEN 'web' THEN EXISTS(SELECT 1 FROM org_members m WHERE m.org_id=p_org_id
  AND m.org_id=(p_target->>'org_id')::UUID AND m.user_id=(p_target->>'user_id')::UUID
  AND m.status='active')
 WHEN 'wecom_user' THEN EXISTS(SELECT 1 FROM wecom_user_mappings m JOIN org_members member
  ON member.org_id=m.org_id AND member.user_id=m.user_id AND member.status='active'
  WHERE m.id=(p_target->>'mapping_id')::UUID AND m.org_id=p_org_id
   AND m.org_id=(p_target->>'org_id')::UUID AND m.corp_id=p_target->>'corp_id'
   AND m.user_id=(p_target->>'mapping_user_id')::UUID
   AND m.wecom_userid=p_target->>'wecom_userid' AND m.channel=p_target->>'channel')
 WHEN 'wecom_group' THEN EXISTS(SELECT 1 FROM wecom_chat_targets g
  WHERE g.id=(p_target->>'target_id')::UUID AND g.org_id=p_org_id
   AND g.org_id=(p_target->>'org_id')::UUID AND g.corp_id=p_target->>'corp_id'
   AND g.chatid=p_target->>'chatid' AND g.chat_type=p_target->>'chat_type' AND g.is_active)
 ELSE FALSE END
$$;

CREATE FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(
 p_org_id UUID,p_scheduled_run_id UUID,p_runtime_run_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE snapshot agent_runtime_scheduled_delivery_snapshots%ROWTYPE;
 runtime_binding agent_runtime_scheduled_delivery_runtime_bindings%ROWTYPE;
 content agent_runtime_scheduled_delivery_contents%ROWTYPE;target RECORD;targets JSONB;intents JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker'
 OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_PROJECTION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO snapshot FROM agent_runtime_scheduled_delivery_snapshots
  WHERE scheduled_run_id=p_scheduled_run_id AND org_id=p_org_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO runtime_binding FROM agent_runtime_scheduled_delivery_runtime_bindings
  WHERE scheduled_run_id=p_scheduled_run_id AND runtime_run_id=p_runtime_run_id;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
 IF NOT EXISTS(SELECT 1 FROM org_members WHERE org_id=p_org_id
  AND user_id=snapshot.user_id AND status='active') THEN
  RETURN jsonb_build_object('outcome','unavailable','reason_code','delivery_member_unavailable');
 END IF;
 FOR target IN SELECT target_key,target_snapshot FROM agent_runtime_scheduled_delivery_targets
  WHERE scheduled_run_id=p_scheduled_run_id ORDER BY ordinal LOOP
  IF NOT _agent_runtime_scheduled_delivery_target_available(p_org_id,target.target_snapshot) THEN
   RETURN jsonb_build_object('outcome','unavailable','reason_code','delivery_target_unavailable',
    'target_key',target.target_key);
  END IF;
 END LOOP;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('target_key',target_key,
  'target_hash',target_hash,'target_type',target_type,'target',target_snapshot,
  'ordinal',ordinal) ORDER BY ordinal),'[]'::JSONB) INTO targets
 FROM agent_runtime_scheduled_delivery_targets WHERE scheduled_run_id=p_scheduled_run_id;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('intent_id',id,'target_key',target_key,
  'target_hash',target_hash,
  'terminal_status',terminal_status,'result_hash',result_hash,'reason_code',reason_code,
  'content_identity_hash',content_identity_hash,'idempotency_key',idempotency_key,
  'status',status,'state_version',state_version,'created_at',created_at)
  ORDER BY target_key),'[]'::JSONB) INTO intents
 FROM agent_runtime_scheduled_delivery_intents WHERE scheduled_run_id=p_scheduled_run_id;
 SELECT * INTO content FROM agent_runtime_scheduled_delivery_contents
  WHERE scheduled_run_id=p_scheduled_run_id AND runtime_run_id=p_runtime_run_id;
 RETURN jsonb_build_object('outcome','found','snapshot',jsonb_build_object(
  'scheduled_run_id',snapshot.scheduled_run_id,'scheduled_task_id',snapshot.scheduled_task_id,
  'runtime_run_id',runtime_binding.runtime_run_id,'org_id',snapshot.org_id,
  'user_id',snapshot.user_id,'runtime_command_id',snapshot.runtime_command_id,
  'profile_state_version',snapshot.profile_state_version,'task_revision',snapshot.task_revision,
  'target_set_hash',snapshot.target_set_hash,'target_count',snapshot.target_count),
  'targets',targets,'content',CASE WHEN content.scheduled_run_id IS NULL THEN NULL ELSE
   jsonb_build_object('model_result_id',content.model_result_id,'terminal_status',content.terminal_status,
    'result_hash',content.result_hash,'reason_code',content.reason_code,
    'artifact_manifest',content.artifact_manifest,'artifact_manifest_hash',content.artifact_manifest_hash,
    'content_identity_hash',content.content_identity_hash) END,'intents',intents);
END $$;

REVOKE ALL ON FUNCTION
 _agent_runtime_scheduled_delivery_normalize(UUID,UUID,JSONB,INTEGER),
 _capture_agent_runtime_scheduled_delivery_snapshot(),
 _bind_agent_runtime_scheduled_delivery_runtime_run(),
 _capture_agent_runtime_scheduled_delivery_intents(),
 _agent_runtime_scheduled_delivery_target_available(UUID,JSONB),
 read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)
 TO everydayai_projection_worker;

RESET ROLE;
