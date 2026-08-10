-- 227_37: Durable Scheduled Runtime WeCom delivery facts; no transport or Secret access.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_delivery_intents IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_intents i
  JOIN agent_runtime_scheduled_delivery_targets t
   ON(t.scheduled_run_id,t.target_key)=(i.scheduled_run_id,i.target_key)
  WHERE t.target_type IN('wecom_user','wecom_group')) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_BACKFILL_REQUIRED' USING ERRCODE='55000';
 END IF;
END $$;

CREATE TABLE agent_runtime_scheduled_wecom_deliveries(
 intent_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_delivery_intents(id) ON DELETE RESTRICT,
 scheduled_run_id UUID NOT NULL REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 target_key TEXT NOT NULL,target_hash TEXT NOT NULL CHECK(target_hash~'^[0-9a-f]{64}$'),
 target_type TEXT NOT NULL CHECK(target_type IN('wecom_user','wecom_group')),
 target_snapshot JSONB NOT NULL CHECK(jsonb_typeof(target_snapshot)='object'),
 content_identity_hash TEXT NOT NULL CHECK(content_identity_hash~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL DEFAULT 1 CHECK(provider_revision>0),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN(
  'pending','claimed','dispatching','accepted','unknown','reconcile_required','retry_wait',
  'partial','completed','failed','cancelled','unavailable')),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 claim_worker_id TEXT CHECK(claim_worker_id IS NULL OR length(claim_worker_id) BETWEEN 1 AND 128),
 claim_request_id UUID UNIQUE,lease_token UUID UNIQUE,lease_expires_at TIMESTAMPTZ,
 reconcile_worker_id TEXT CHECK(reconcile_worker_id IS NULL OR length(reconcile_worker_id) BETWEEN 1 AND 128),
 reconcile_request_id UUID UNIQUE,reconcile_token UUID UNIQUE,reconcile_lease_expires_at TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ,terminal_reason_code TEXT CHECK(
  terminal_reason_code IS NULL OR terminal_reason_code~'^[a-z0-9_]{1,80}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(intent_id,target_hash,content_identity_hash,provider_revision),
 CHECK((lease_token IS NULL)=(lease_expires_at IS NULL)),
 CHECK((lease_token IS NULL)=(claim_worker_id IS NULL)),
 CHECK((reconcile_token IS NULL)=(reconcile_lease_expires_at IS NULL)),
 CHECK((reconcile_token IS NULL)=(reconcile_worker_id IS NULL))
);
CREATE TABLE agent_runtime_scheduled_wecom_delivery_items(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),intent_id UUID NOT NULL
  REFERENCES agent_runtime_scheduled_wecom_deliveries(intent_id) ON DELETE RESTRICT,
 item_key TEXT NOT NULL UNIQUE CHECK(item_key~'^[0-9a-f]{64}$'),ordinal INTEGER NOT NULL CHECK(ordinal>0),
 item_kind TEXT NOT NULL CHECK(item_kind IN('text','artifact_identity')),
 source_id UUID NOT NULL,source_revision BIGINT NOT NULL CHECK(source_revision>=0),
 source_identity_hash TEXT NOT NULL CHECK(source_identity_hash~'^[0-9a-f]{64}$'),
 content_identity_hash TEXT NOT NULL CHECK(content_identity_hash~'^[0-9a-f]{64}$'),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN(
  'pending','dispatching','accepted','unknown','reconcile_required','retry_wait','failed','cancelled')),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 next_attempt_at TIMESTAMPTZ,terminal_reason_code TEXT CHECK(
  terminal_reason_code IS NULL OR terminal_reason_code~'^[a-z0-9_]{1,80}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(intent_id,ordinal),UNIQUE(intent_id,item_key,content_identity_hash)
);
CREATE TABLE agent_runtime_scheduled_wecom_dispatch_attempts(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),item_id UUID NOT NULL
  REFERENCES agent_runtime_scheduled_wecom_delivery_items(id) ON DELETE RESTRICT,
 attempt_number INTEGER NOT NULL CHECK(attempt_number>0),
 provider_request_id TEXT NOT NULL UNIQUE CHECK(length(provider_request_id) BETWEEN 8 AND 200),
 idempotency_key TEXT NOT NULL UNIQUE CHECK(idempotency_key~'^[0-9a-f]{64}$'),
 provider_revision BIGINT NOT NULL CHECK(provider_revision>0),
 status TEXT NOT NULL CHECK(status IN('prepared','dispatch_started','accepted','rejected','unknown')),
 dispatch_phase TEXT NOT NULL CHECK(dispatch_phase IN('prepared','external_request_started','receipt_recorded','ambiguous')),
 receipt_type TEXT CHECK(receipt_type IS NULL OR receipt_type~'^[a-z0-9_]{1,80}$'),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR receipt_hash~'^[0-9a-f]{64}$'),
 receipt_code TEXT CHECK(receipt_code IS NULL OR receipt_code~'^[a-z0-9_]{1,80}$'),
 was_ambiguous BOOLEAN NOT NULL DEFAULT FALSE,prepared_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 dispatch_started_at TIMESTAMPTZ,unknown_at TIMESTAMPTZ,resolved_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(item_id,attempt_number),CHECK((status='prepared')=(dispatch_started_at IS NULL)),
 CHECK((receipt_hash IS NULL)=(receipt_type IS NULL)),
 CHECK(status NOT IN('accepted','rejected') OR receipt_hash IS NOT NULL),
 CHECK(status<>'prepared' OR(dispatch_phase='prepared' AND receipt_hash IS NULL
  AND unknown_at IS NULL AND resolved_at IS NULL AND NOT was_ambiguous)),
 CHECK(status<>'dispatch_started' OR(dispatch_phase='external_request_started'
  AND receipt_hash IS NULL AND unknown_at IS NULL AND resolved_at IS NULL AND NOT was_ambiguous)),
 CHECK(status<>'unknown' OR(dispatch_phase='ambiguous' AND unknown_at IS NOT NULL
  AND receipt_hash IS NULL AND resolved_at IS NULL AND was_ambiguous)),
 CHECK(status NOT IN('accepted','rejected') OR(dispatch_phase='receipt_recorded'
  AND dispatch_started_at IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX idx_runtime_scheduled_wecom_claim ON agent_runtime_scheduled_wecom_deliveries(
 status,next_attempt_at,lease_expires_at,created_at) WHERE status IN('pending','claimed','retry_wait','partial');
CREATE INDEX idx_runtime_scheduled_wecom_reconcile ON agent_runtime_scheduled_wecom_deliveries(
 status,reconcile_lease_expires_at,updated_at) WHERE status IN('unknown','reconcile_required');

ALTER TABLE agent_runtime_scheduled_wecom_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_delivery_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_delivery_items FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_dispatch_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_wecom_dispatch_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_wecom_deliveries_owner ON agent_runtime_scheduled_wecom_deliveries
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_wecom_items_owner ON agent_runtime_scheduled_wecom_delivery_items
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_wecom_attempts_owner ON agent_runtime_scheduled_wecom_dispatch_attempts
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON agent_runtime_scheduled_wecom_deliveries,agent_runtime_scheduled_wecom_delivery_items,
 agent_runtime_scheduled_wecom_dispatch_attempts FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,
 everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker;

CREATE FUNCTION _agent_runtime_scheduled_wecom_identity_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE mutable TEXT[];old_identity JSONB;new_identity JSONB;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_FACT_IMMUTABLE' USING ERRCODE='55000'; END IF;
 mutable:=CASE TG_TABLE_NAME
  WHEN 'agent_runtime_scheduled_wecom_deliveries' THEN ARRAY['status','state_version','claim_worker_id',
   'claim_request_id','lease_token','lease_expires_at','reconcile_worker_id','reconcile_request_id',
   'reconcile_token','reconcile_lease_expires_at','next_attempt_at','terminal_reason_code','updated_at']
  WHEN 'agent_runtime_scheduled_wecom_delivery_items' THEN ARRAY['status','state_version','next_attempt_at',
   'terminal_reason_code','updated_at']
  ELSE ARRAY['status','dispatch_phase','receipt_type','receipt_hash','receipt_code','was_ambiguous',
   'dispatch_started_at','unknown_at','resolved_at','updated_at'] END;
 old_identity:=to_jsonb(OLD)-mutable;new_identity:=to_jsonb(NEW)-mutable;
 IF old_identity IS DISTINCT FROM new_identity THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_dispatch_attempts' THEN
  IF OLD.status IN('accepted','rejected') OR NOT((OLD.status,NEW.status) IN(
   ('prepared','prepared'),('prepared','dispatch_started'),('dispatch_started','dispatch_started'),
   ('dispatch_started','accepted'),('dispatch_started','rejected'),('dispatch_started','unknown'),
   ('unknown','unknown'),('unknown','accepted'),('unknown','rejected'))) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_ATTEMPT_TRANSITION_INVALID' USING ERRCODE='55000';
  END IF;
  IF OLD.receipt_hash IS NOT NULL AND(OLD.receipt_type,OLD.receipt_hash,OLD.receipt_code)
   IS DISTINCT FROM(NEW.receipt_type,NEW.receipt_hash,NEW.receipt_code) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECEIPT_IMMUTABLE' USING ERRCODE='55000';
  END IF;
  IF(OLD.dispatch_started_at IS NOT NULL AND OLD.dispatch_started_at IS DISTINCT FROM NEW.dispatch_started_at)
  OR(OLD.unknown_at IS NOT NULL AND OLD.unknown_at IS DISTINCT FROM NEW.unknown_at)
  OR(OLD.resolved_at IS NOT NULL AND OLD.resolved_at IS DISTINCT FROM NEW.resolved_at)
  OR(OLD.was_ambiguous AND NOT NEW.was_ambiguous) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_EVIDENCE_IMMUTABLE' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER runtime_scheduled_wecom_delivery_identity_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_deliveries FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_wecom_identity_guard();
CREATE TRIGGER runtime_scheduled_wecom_item_identity_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_delivery_items FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_wecom_identity_guard();
CREATE TRIGGER runtime_scheduled_wecom_attempt_identity_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_wecom_dispatch_attempts FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_wecom_identity_guard();

CREATE FUNCTION _initialize_agent_runtime_scheduled_wecom_delivery() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE target agent_runtime_scheduled_delivery_targets%ROWTYPE;
 content agent_runtime_scheduled_delivery_contents%ROWTYPE;entry JSONB;source UUID;
 revision BIGINT;identity TEXT;item_key TEXT;position INTEGER:=1;
BEGIN
 SELECT * INTO target FROM agent_runtime_scheduled_delivery_targets
  WHERE scheduled_run_id=NEW.scheduled_run_id AND target_key=NEW.target_key;
 IF target.target_type NOT IN('wecom_user','wecom_group') THEN RETURN NEW; END IF;
 SELECT * INTO content FROM agent_runtime_scheduled_delivery_contents
  WHERE scheduled_run_id=NEW.scheduled_run_id AND content_identity_hash=NEW.content_identity_hash;
 IF target.target_hash IS DISTINCT FROM NEW.target_hash OR content.scheduled_run_id IS NULL
 OR(content.runtime_run_id,content.terminal_status,content.result_hash,content.reason_code)
  IS DISTINCT FROM(NEW.runtime_run_id,NEW.terminal_status,NEW.result_hash,NEW.reason_code) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_INITIALIZATION_FENCED' USING ERRCODE='55000';
 END IF;
 INSERT INTO agent_runtime_scheduled_wecom_deliveries(intent_id,scheduled_run_id,runtime_run_id,
  scheduled_task_id,org_id,user_id,target_key,target_hash,target_type,target_snapshot,content_identity_hash)
 VALUES(NEW.id,NEW.scheduled_run_id,NEW.runtime_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,
  NEW.target_key,NEW.target_hash,target.target_type,target.target_snapshot,NEW.content_identity_hash);
 source:=coalesce(content.model_result_id,NEW.runtime_run_id);revision:=1;
 identity:=coalesce(content.result_hash,content.content_identity_hash);
 item_key:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
  'intent_id',NEW.id,'content_identity_hash',NEW.content_identity_hash,'kind','text',
  'source_id',source,'source_revision',revision,'source_identity_hash',identity)),'UTF8'),'sha256'),'hex');
 INSERT INTO agent_runtime_scheduled_wecom_delivery_items(intent_id,item_key,ordinal,item_kind,
  source_id,source_revision,source_identity_hash,content_identity_hash)
 VALUES(NEW.id,item_key,position,'text',source,revision,identity,NEW.content_identity_hash);
 FOR entry IN SELECT value FROM jsonb_array_elements(content.artifact_manifest) LOOP
  position:=position+1;source:=(entry->>'artifact_id')::UUID;
  revision:=(entry->>'materialize_revision')::BIGINT;identity:=entry->>'content_hash';
  IF identity!~'^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_ARTIFACT_IDENTITY_INVALID' USING ERRCODE='55000';
  END IF;
  item_key:=encode(digest(convert_to(_agent_runtime_scheduled_canonical_json(jsonb_build_object(
   'intent_id',NEW.id,'content_identity_hash',NEW.content_identity_hash,'kind','artifact_identity',
   'source_id',source,'source_revision',revision,'source_identity_hash',identity)),'UTF8'),'sha256'),'hex');
  INSERT INTO agent_runtime_scheduled_wecom_delivery_items(intent_id,item_key,ordinal,item_kind,
   source_id,source_revision,source_identity_hash,content_identity_hash)
  VALUES(NEW.id,item_key,position,'artifact_identity',source,revision,identity,NEW.content_identity_hash);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_runtime_scheduled_wecom_delivery AFTER INSERT
 ON agent_runtime_scheduled_delivery_intents FOR EACH ROW
 EXECUTE FUNCTION _initialize_agent_runtime_scheduled_wecom_delivery();

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_wecom_identity_guard(),
 _initialize_agent_runtime_scheduled_wecom_delivery()
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,
 everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

RESET ROLE;
