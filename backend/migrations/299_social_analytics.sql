-- Phase five: immutable observations and operator-confirmed learning.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE social_production_runs ADD COLUMN usage jsonb NOT NULL DEFAULT '{}';
CREATE TABLE social_metric_collections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, job_id uuid NOT NULL,
 intent_id uuid NOT NULL, published_at timestamptz, content_type text NOT NULL,
 UNIQUE(job_id), UNIQUE(id,org_id),
 FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id),
 FOREIGN KEY(intent_id,org_id) REFERENCES social_publish_intents(id,org_id));
CREATE TABLE social_metric_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, intent_id uuid NOT NULL,
 connection_id uuid NOT NULL, account_id text NOT NULL, remote_id text NOT NULL,
 platform text NOT NULL, content_type text NOT NULL, observed_at timestamptz NOT NULL,
 published_at timestamptz, metrics jsonb NOT NULL, raw_metrics jsonb NOT NULL DEFAULT '{}',
 coverage jsonb NOT NULL, source text NOT NULL, adapter_version text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 dedupe_key text NOT NULL, UNIQUE(org_id,dedupe_key), UNIQUE(id,org_id),
 FOREIGN KEY(intent_id,org_id) REFERENCES social_publish_intents(id,org_id),
 FOREIGN KEY(connection_id,org_id) REFERENCES reach_connections(id,org_id));
CREATE INDEX social_metrics_account_time ON social_metric_snapshots(org_id,connection_id,observed_at DESC);
CREATE INDEX social_metrics_intent_time ON social_metric_snapshots(org_id,intent_id,observed_at DESC);
CREATE TABLE social_metric_imports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, owner_id uuid NOT NULL,
 file_hash text NOT NULL, source text NOT NULL, mapping jsonb NOT NULL, preview jsonb NOT NULL,
 preview_hash text NOT NULL, status text NOT NULL DEFAULT 'preview', created_at timestamptz NOT NULL DEFAULT now(),
 confirmed_at timestamptz, UNIQUE(id,org_id));
CREATE TABLE social_review_suggestions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, run_id uuid NOT NULL,
 profile_revision_id uuid NOT NULL, kind text NOT NULL CHECK(kind IN ('experience','topic')),
 original_text text NOT NULL, evidence jsonb NOT NULL, decision text NOT NULL DEFAULT 'pending'
 CHECK(decision IN ('pending','accept','reject')), edited_text text, decided_by uuid, decided_at timestamptz, result jsonb,
 UNIQUE(id,org_id), FOREIGN KEY(run_id,org_id) REFERENCES social_production_runs(id,org_id),
 FOREIGN KEY(profile_revision_id,org_id) REFERENCES social_profile_revisions(id,org_id));
CREATE TABLE social_analytics_schedules (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, owner_id uuid NOT NULL,
 intent_id uuid NOT NULL, config jsonb NOT NULL, skill_pin jsonb,
 enabled boolean NOT NULL DEFAULT false, next_at timestamptz NOT NULL DEFAULT now(),
 next_review_at timestamptz NOT NULL DEFAULT now(), last_job_id uuid,
 error text NOT NULL DEFAULT '', UNIQUE(org_id,intent_id), UNIQUE(id,org_id),
 FOREIGN KEY(intent_id,org_id) REFERENCES social_publish_intents(id,org_id),
 FOREIGN KEY(last_job_id,org_id) REFERENCES social_research_jobs(id,org_id));
CREATE INDEX social_analytics_schedule_due ON social_analytics_schedules(next_at) WHERE enabled;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['social_metric_collections','social_metric_snapshots','social_metric_imports','social_review_suggestions','social_analytics_schedules'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY scoped ON %I USING(reach_active_member(org_id)) WITH CHECK(reach_active_member(org_id))',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
GRANT UPDATE ON social_metric_imports,social_review_suggestions,social_analytics_schedules TO everydayai,everydayai_runtime;
-- Immutable evidence, even when the application owner role is used.
CREATE FUNCTION social_metric_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'metric snapshot is immutable'; END $$;
CREATE TRIGGER social_metric_immutable BEFORE UPDATE OR DELETE ON social_metric_snapshots FOR EACH ROW EXECUTE FUNCTION social_metric_immutable();
CREATE POLICY analytics_schedule_worker ON social_analytics_schedules USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE FUNCTION social_analytics_due() RETURNS TABLE(org_id uuid,owner_id uuid,schedule_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT s.org_id,s.owner_id,s.id FROM social_analytics_schedules s
 WHERE current_setting('app.access_kind',true)='worker' AND s.enabled AND s.next_at<=now()
 ORDER BY s.next_at LIMIT 10
$$;
REVOKE ALL ON FUNCTION social_analytics_due() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION social_analytics_due() TO everydayai;

CREATE TABLE social_analytics_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, owner_id uuid NOT NULL,
 object_id uuid NOT NULL, state text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), notified_at timestamptz,
 UNIQUE(org_id,object_id,state));
ALTER TABLE social_analytics_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_analytics_events FORCE ROW LEVEL SECURITY;
CREATE POLICY scoped ON social_analytics_events USING(reach_active_member(org_id)) WITH CHECK(reach_active_member(org_id));
CREATE POLICY worker_events ON social_analytics_events USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
GRANT SELECT,INSERT ON social_analytics_events TO everydayai,everydayai_runtime;
CREATE FUNCTION social_analytics_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.status IN ('completed','partial','failed','needs_input','paused_auth','paused_platform') AND NEW.status IS DISTINCT FROM OLD.status THEN
 IF TG_TABLE_NAME='social_production_runs' THEN
   IF NEW.kind NOT IN ('postmortem','comment_insights','strategy') THEN RETURN NEW; END IF;
 ELSE
   IF NEW.plan->>'purpose' IS DISTINCT FROM 'metrics' THEN RETURN NEW; END IF;
 END IF;
 INSERT INTO social_analytics_events(org_id,owner_id,object_id,state) VALUES(NEW.org_id,NEW.owner_id,NEW.id,NEW.status) ON CONFLICT DO NOTHING;
 END IF; RETURN NEW; END $$;
CREATE TRIGGER analytics_research_event AFTER UPDATE OF status ON social_research_jobs FOR EACH ROW EXECUTE FUNCTION social_analytics_event();
CREATE TRIGGER analytics_review_event AFTER UPDATE OF status ON social_production_runs FOR EACH ROW EXECUTE FUNCTION social_analytics_event();
CREATE FUNCTION social_analytics_notifications() RETURNS TABLE(org_id uuid,owner_id uuid,object_id uuid,state text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 UPDATE social_analytics_events e SET notified_at=now() WHERE e.id IN
 (SELECT n.id FROM social_analytics_events n WHERE current_setting('app.access_kind',true)='worker' AND n.notified_at IS NULL
 AND EXISTS(SELECT 1 FROM org_members m JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=m.org_id
 WHERE m.org_id=n.org_id AND m.user_id=n.owner_id AND m.status='active' AND u.status='active' AND o.status='active')
 ORDER BY n.created_at FOR UPDATE SKIP LOCKED LIMIT 20) RETURNING e.org_id,e.owner_id,e.object_id,e.state
$$;
REVOKE ALL ON FUNCTION social_analytics_notifications() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION social_analytics_notifications() TO everydayai;
CREATE OR REPLACE FUNCTION social_analytics_due() RETURNS TABLE(org_id uuid,owner_id uuid,schedule_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH revoked AS (UPDATE social_analytics_schedules s SET enabled=false,error='CREATOR_ACCESS_REVOKED'
 WHERE current_setting('app.access_kind',true)='worker' AND s.enabled AND NOT EXISTS
 (SELECT 1 FROM org_members m JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=m.org_id
 WHERE m.org_id=s.org_id AND m.user_id=s.owner_id AND m.status='active' AND u.status='active' AND o.status='active'))
 SELECT s.org_id,s.owner_id,s.id FROM social_analytics_schedules s
 WHERE current_setting('app.access_kind',true)='worker' AND s.enabled AND s.next_at<=now()
 AND EXISTS(SELECT 1 FROM org_members m JOIN users u ON u.id=m.user_id JOIN organizations o ON o.id=m.org_id
 WHERE m.org_id=s.org_id AND m.user_id=s.owner_id AND m.status='active' AND u.status='active' AND o.status='active')
 ORDER BY s.next_at LIMIT 10
$$;
-- Tenant foreign keys alone cannot guarantee that an observation's account and
-- remote identity belong to its publication. Validate the immutable binding too.
CREATE FUNCTION social_metric_binding() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM social_publish_intents i JOIN reach_connections c ON c.id=i.connection_id AND c.org_id=i.org_id
 WHERE i.id=NEW.intent_id AND i.org_id=NEW.org_id AND i.state='verified' AND i.connection_id=NEW.connection_id
 AND c.account_id=NEW.account_id AND c.platform=NEW.platform AND i.verification->>'remote_id'=NEW.remote_id) THEN
 RAISE EXCEPTION 'metric publication identity mismatch'; END IF;
 IF NEW.content_type NOT IN ('image','video','text') OR (NEW.published_at IS NOT NULL AND NEW.published_at>NEW.observed_at) THEN
 RAISE EXCEPTION 'invalid metric scope or publication time'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER social_metric_binding BEFORE INSERT ON social_metric_snapshots FOR EACH ROW EXECUTE FUNCTION social_metric_binding();

CREATE INDEX social_review_change ON social_production_runs(org_id,kind,(input->>'change_hash')) WHERE kind IN ('postmortem','comment_insights','strategy');
CREATE INDEX social_suggestions_run ON social_review_suggestions(org_id,run_id);
CREATE INDEX social_collections_intent ON social_metric_collections(org_id,intent_id);
CREATE INDEX social_import_actor ON social_metric_imports(org_id,owner_id,created_at DESC);
-- Disabled analytics cannot occupy the bounded phase-one/four discovery queue.
CREATE OR REPLACE FUNCTION social_ops_due() RETURNS TABLE(org_id uuid,owner_id uuid,job_id uuid,run_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH revoked_jobs AS (
 UPDATE social_research_jobs j SET status='paused_auth',last_error='CREATOR_ACCESS_REVOKED'
 WHERE current_setting('app.access_kind',true)='worker' AND j.status IN ('queued','running','waiting_budget')
 AND NOT EXISTS(SELECT 1 FROM org_members m JOIN organizations o ON o.id=m.org_id JOIN users u ON u.id=m.user_id
 WHERE m.org_id=j.org_id AND m.user_id=j.owner_id AND m.status='active' AND o.status='active' AND u.status='active')
 ), revoked_runs AS (
 UPDATE social_production_runs r SET status='needs_input',error='CREATOR_ACCESS_REVOKED'
 WHERE current_setting('app.access_kind',true)='worker' AND r.status IN ('queued','running')
 AND NOT EXISTS(SELECT 1 FROM org_members m JOIN organizations o ON o.id=m.org_id JOIN users u ON u.id=m.user_id
 WHERE m.org_id=r.org_id AND m.user_id=r.owner_id AND m.status='active' AND o.status='active' AND u.status='active')
 )
 SELECT q.org_id,q.owner_id,q.job_id,q.run_id FROM (SELECT j.org_id,j.owner_id,j.id AS job_id,NULL::uuid AS run_id FROM social_research_jobs j
 WHERE current_setting('app.access_kind',true)='worker' AND
 (j.plan->>'purpose' IS DISTINCT FROM 'metrics' OR current_setting('app.social_analytics_enabled',true)='true') AND
 ((j.status IN ('queued','running','waiting_budget') AND j.next_eligible_at<=now()) OR
 (j.status IN ('completed','partial','failed') AND j.next_occurrence_at<=now()))
 ORDER BY j.next_eligible_at,j.created_at LIMIT 10) q
 UNION ALL
 SELECT p.org_id,p.owner_id,p.job_id,p.run_id FROM (SELECT r.org_id,r.owner_id,r.job_id,r.id AS run_id FROM social_production_runs r
 WHERE current_setting('app.access_kind',true)='worker' AND
 (r.kind NOT IN ('postmortem','comment_insights','strategy') OR current_setting('app.social_analytics_enabled',true)='true') AND (r.status='queued' OR
 (r.status='running' AND r.lease_expires_at<now())) ORDER BY r.created_at LIMIT 10) p
$$;

RESET ROLE;
