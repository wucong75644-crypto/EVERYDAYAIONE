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
CREATE POLICY runtime_scheduled_delivery_intents_owner
 ON agent_runtime_scheduled_delivery_intents FOR ALL TO everydayai_owner
 USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_delivery_snapshots,
 agent_runtime_scheduled_delivery_targets,
 agent_runtime_scheduled_delivery_runtime_bindings,
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
CREATE TRIGGER runtime_scheduled_delivery_intent_immutable BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_delivery_intents FOR EACH ROW
 EXECUTE FUNCTION _runtime_scheduler_immutable_fact();

CREATE FUNCTION _agent_runtime_scheduled_delivery_normalize(
 p_org_id UUID,p_user_id UUID,p_target JSONB,p_depth INTEGER DEFAULT 0)
RETURNS TABLE(target_key TEXT,target_hash TEXT,target_type TEXT,target_snapshot JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE kind TEXT;identifier TEXT;item JSONB;canonical JSONB;
BEGIN
 IF p_depth NOT BETWEEN 0 AND 4 OR jsonb_typeof(p_target)<>'object'
 OR NOT _runtime_scheduler_push_target_allowed(p_org_id,p_user_id,p_target,p_depth) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_DENIED' USING ERRCODE='42501';
 END IF;
 kind:=p_target->>'type';
 IF kind='multi' THEN
  FOR item IN SELECT value FROM jsonb_array_elements(p_target->'targets') LOOP
   RETURN QUERY SELECT * FROM _agent_runtime_scheduled_delivery_normalize(
    p_org_id,p_user_id,item,p_depth+1);
  END LOOP;
  RETURN;
 ELSIF kind='web' THEN
  BEGIN identifier:=(p_target->>'user_id')::UUID::TEXT;
  EXCEPTION WHEN invalid_text_representation THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
  END;
  canonical:=jsonb_build_object('type','web','user_id',identifier);
 ELSIF kind='wecom_user' THEN
  identifier:=btrim(p_target->>'wecom_userid');
  canonical:=jsonb_build_object('type','wecom_user','wecom_userid',identifier);
 ELSIF kind='wecom_group' THEN
  identifier:=btrim(p_target->>'chatid');
  canonical:=jsonb_build_object('type','wecom_group','chatid',identifier);
 ELSE
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
 END IF;
 IF identifier IS NULL OR length(identifier) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_INVALID' USING ERRCODE='22023';
 END IF;
 target_key:=kind||':'||identifier;
 target_hash:=encode(digest(convert_to(
  _agent_runtime_scheduled_canonical_json(canonical),'UTF8'),'sha256'),'hex');
 target_type:=kind;target_snapshot:=canonical;
 RETURN NEXT;
END $$;

CREATE FUNCTION _capture_agent_runtime_scheduled_delivery_snapshot() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE task scheduled_tasks%ROWTYPE;binding agent_runtime_scheduled_run_bindings%ROWTYPE;
 profile agent_runtime_scheduled_execution_profiles%ROWTYPE;raw_count INTEGER;targets JSONB;
 target_set_hash TEXT;item JSONB;position INTEGER:=0;
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
 SELECT count(*) INTO raw_count FROM _agent_runtime_scheduled_delivery_normalize(
  NEW.org_id,NEW.user_id,task.push_target,0);
 IF raw_count NOT BETWEEN 1 AND 20 THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_COUNT_INVALID' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM _agent_runtime_scheduled_delivery_normalize(
   NEW.org_id,NEW.user_id,task.push_target,0) normalized
   GROUP BY normalized.target_key HAVING count(DISTINCT normalized.target_hash)>1) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_DELIVERY_TARGET_CONFLICT' USING ERRCODE='55000';
 END IF;
 SELECT jsonb_agg(to_jsonb(normalized) ORDER BY normalized.target_key) INTO targets FROM(
  SELECT DISTINCT ON(target_key) target_key,target_hash,target_type,target_snapshot
  FROM _agent_runtime_scheduled_delivery_normalize(NEW.org_id,NEW.user_id,task.push_target,0)
  ORDER BY target_key,target_hash
 ) normalized;
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
 prior agent_runtime_scheduled_delivery_intents%ROWTYPE;reason TEXT;
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
 content_hash:=CASE WHEN NEW.terminal_status='completed' THEN NEW.result_hash ELSE
  encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
   'terminal_status',NEW.terminal_status,'reason_code',reason)),'UTF8'),'sha256'),'hex') END;
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

CREATE FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(
 p_org_id UUID,p_scheduled_run_id UUID,p_runtime_run_id UUID) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE snapshot agent_runtime_scheduled_delivery_snapshots%ROWTYPE;
 runtime_binding agent_runtime_scheduled_delivery_runtime_bindings%ROWTYPE;
 targets JSONB;intents JSONB;
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
 RETURN jsonb_build_object('outcome','found','snapshot',jsonb_build_object(
  'scheduled_run_id',snapshot.scheduled_run_id,'scheduled_task_id',snapshot.scheduled_task_id,
  'runtime_run_id',runtime_binding.runtime_run_id,'org_id',snapshot.org_id,
  'user_id',snapshot.user_id,'runtime_command_id',snapshot.runtime_command_id,
  'profile_state_version',snapshot.profile_state_version,'task_revision',snapshot.task_revision,
  'target_set_hash',snapshot.target_set_hash,'target_count',snapshot.target_count),
  'targets',targets,'intents',intents);
END $$;

REVOKE ALL ON FUNCTION
 _agent_runtime_scheduled_delivery_normalize(UUID,UUID,JSONB,INTEGER),
 _capture_agent_runtime_scheduled_delivery_snapshot(),
 _bind_agent_runtime_scheduled_delivery_runtime_run(),
 _capture_agent_runtime_scheduled_delivery_intents(),
 read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)
 TO everydayai_projection_worker;

RESET ROLE;
