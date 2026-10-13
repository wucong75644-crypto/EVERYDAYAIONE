-- Narrow final-version authorization; ordinary scheduled dangerous tools remain forbidden.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE social_publish_intents
 ADD COLUMN scheduled_at timestamptz,
 ADD COLUMN schedule_timezone text,
 ADD COLUMN authorized_until timestamptz,
 ADD COLUMN grant_hash text,
 ADD COLUMN grant_consumed_at timestamptz,
 ADD COLUMN lease_token uuid,
 ADD COLUMN lease_expires_at timestamptz,
 ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE social_publish_intents DROP CONSTRAINT social_publish_intents_state_check;
ALTER TABLE social_publish_intents ADD CONSTRAINT social_publish_intents_state_check CHECK(state IN
 ('awaiting_confirmation','scheduled','preparing','waiting_budget','paused','dispatching','submitted','verified','uncertain','failed','cancelled','invalidated','not_published','expired'));
ALTER TABLE social_publish_intents ADD CONSTRAINT social_schedule_window CHECK
 (scheduled_at IS NULL OR (schedule_timezone IS NOT NULL AND authorized_until>=scheduled_at
 AND authorized_until<=scheduled_at+interval '1 hour'));
CREATE INDEX social_publish_due ON social_publish_intents(next_attempt_at,scheduled_at) WHERE scheduled_at IS NOT NULL AND state IN ('scheduled','waiting_budget','preparing','dispatching','paused');
CREATE TABLE social_publish_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, org_id uuid NOT NULL, intent_id uuid NOT NULL,
 state text NOT NULL, reason text NOT NULL, actor_id uuid, notified boolean NOT NULL DEFAULT false, snapshot jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(intent_id,org_id) REFERENCES social_publish_intents(id,org_id));
ALTER TABLE social_publish_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_publish_events FORCE ROW LEVEL SECURITY;
CREATE POLICY scoped ON social_publish_events USING(reach_active_member(org_id)) WITH CHECK(reach_active_member(org_id));
GRANT SELECT ON social_publish_events TO everydayai,everydayai_runtime;
CREATE FUNCTION social_publish_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='INSERT' OR NEW.state IS DISTINCT FROM OLD.state OR NEW.approval_hash IS DISTINCT FROM OLD.approval_hash THEN
 INSERT INTO social_publish_events(org_id,intent_id,state,reason,actor_id,snapshot)
 VALUES(NEW.org_id,NEW.id,NEW.state,NEW.error,NULLIF(current_setting('app.actor_user_id',true),'')::uuid,
 jsonb_build_object('approval_hash',NEW.approval_hash,'revision',NEW.revision,'connection_id',NEW.connection_id,
 'credential_version',NEW.credential_version,'visibility',NEW.visibility,'scheduled_at',NEW.scheduled_at,
 'schedule_timezone',NEW.schedule_timezone,'authorized_until',NEW.authorized_until,'approved_by',NEW.approved_by,
 'data',NEW.payload->'data'));
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION social_publish_event() FROM PUBLIC;
CREATE TRIGGER social_publish_event AFTER INSERT OR UPDATE ON social_publish_intents FOR EACH ROW EXECUTE FUNCTION social_publish_event();
-- Mutation revokes approvals atomically with content version changes.
CREATE FUNCTION social_revoke_changed_content() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.current_revision<>OLD.current_revision THEN
 UPDATE social_publish_intents SET state='invalidated',grant_hash=NULL,lease_token=NULL,error='CONTENT_CHANGED',updated_at=now()
 WHERE content_id=NEW.id AND org_id=NEW.org_id AND scheduled_at IS NOT NULL AND state IN ('awaiting_confirmation','scheduled','waiting_budget','paused','preparing');
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION social_revoke_changed_content() FROM PUBLIC;
CREATE TRIGGER social_revoke_changed_content AFTER UPDATE ON social_contents FOR EACH ROW EXECUTE FUNCTION social_revoke_changed_content();
CREATE FUNCTION social_publish_due() RETURNS TABLE(org_id uuid,owner_id uuid,intent_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF current_setting('app.access_kind',true) IS DISTINCT FROM 'worker' THEN RETURN; END IF;
 UPDATE social_publish_intents i SET state='expired',grant_hash=NULL,lease_token=NULL,error='AUTHORIZATION_EXPIRED',updated_at=now()
 WHERE scheduled_at IS NOT NULL AND state IN ('scheduled','preparing','waiting_budget','paused') AND authorized_until<now();
 UPDATE social_publish_intents SET state='uncertain',error='DISPATCH_INTERRUPTED',updated_at=now()
 WHERE scheduled_at IS NOT NULL AND state='dispatching' AND lease_expires_at<now();
 UPDATE social_publish_intents i SET state='paused',grant_hash=NULL,lease_token=NULL,error='CREATOR_ACCESS_REVOKED',updated_at=now()
 WHERE scheduled_at IS NOT NULL AND state IN ('scheduled','preparing','waiting_budget') AND NOT EXISTS
 (SELECT 1 FROM org_members m JOIN organizations o ON o.id=m.org_id JOIN users u ON u.id=m.user_id
 WHERE m.org_id=i.org_id AND m.user_id=i.owner_id AND m.status='active' AND o.status='active' AND u.status='active');
 RETURN QUERY SELECT i.org_id,i.owner_id,i.id FROM social_publish_intents i
 WHERE i.scheduled_at<=now() AND i.authorized_until>=now() AND i.grant_hash IS NOT NULL
 AND ((i.state IN ('scheduled','waiting_budget') AND i.next_attempt_at<=now()) OR (i.state='preparing' AND i.lease_expires_at<now()))
 ORDER BY i.scheduled_at LIMIT 10;
END $$;
REVOKE ALL ON FUNCTION social_publish_due() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION social_publish_due() TO everydayai;
CREATE POLICY social_publish_discovery ON social_publish_intents FOR SELECT USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE POLICY social_publish_maintenance ON social_publish_intents FOR UPDATE USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker') WITH CHECK(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE POLICY social_publish_event_worker ON social_publish_events FOR ALL USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker') WITH CHECK(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE FUNCTION social_revoke_changed_connection() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.credential_version<>OLD.credential_version OR NEW.status<>OLD.status THEN
 UPDATE social_publish_intents SET state='paused',grant_hash=NULL,lease_token=NULL,error='CONNECTION_CHANGED',updated_at=now()
 WHERE connection_id=NEW.id AND org_id=NEW.org_id AND scheduled_at IS NOT NULL AND state IN ('awaiting_confirmation','scheduled','waiting_budget','paused','preparing');
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION social_revoke_changed_connection() FROM PUBLIC;
CREATE TRIGGER social_revoke_changed_connection AFTER UPDATE ON reach_connections FOR EACH ROW EXECUTE FUNCTION social_revoke_changed_connection();
CREATE FUNCTION social_publish_notifications() RETURNS TABLE(org_id uuid,owner_id uuid,intent_id uuid,state text,reason text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 WITH picked AS (SELECT e.id FROM social_publish_events e WHERE current_setting('app.access_kind',true)='worker' AND NOT e.notified
 AND EXISTS(SELECT 1 FROM social_publish_intents i WHERE i.id=e.intent_id AND i.scheduled_at IS NOT NULL)
 AND e.state IN ('scheduled','waiting_budget','paused','submitted','verified','uncertain','failed','cancelled','expired','invalidated')
 ORDER BY e.id LIMIT 20 FOR UPDATE SKIP LOCKED), claimed AS (UPDATE social_publish_events e SET notified=true FROM picked p WHERE e.id=p.id RETURNING e.*)
 SELECT e.org_id,i.owner_id,e.intent_id,e.state,e.reason FROM claimed e JOIN social_publish_intents i ON i.id=e.intent_id AND i.org_id=e.org_id
$$;
REVOKE ALL ON FUNCTION social_publish_notifications() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION social_publish_notifications() TO everydayai;

RESET ROLE;
