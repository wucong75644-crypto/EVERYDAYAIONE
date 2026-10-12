-- Immediate interactive publishing only; no background publishing grants.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE social_production_runs ADD COLUMN client_request_id uuid;
CREATE UNIQUE INDEX social_run_request ON social_production_runs(org_id,client_request_id);
CREATE TABLE social_media_assets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL,
 content_id uuid NOT NULL, owner_id uuid NOT NULL, content_revision integer NOT NULL,
 path text NOT NULL, sha256 text NOT NULL, width integer NOT NULL, height integer NOT NULL,
 source text NOT NULL, rights text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,org_id),
 FOREIGN KEY(content_id,org_id) REFERENCES social_contents(id,org_id));
CREATE TABLE social_package_checks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, content_id uuid NOT NULL,
 revision integer NOT NULL, package_hash text NOT NULL, structural jsonb NOT NULL,
 semantic_run_id uuid NOT NULL, created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,org_id), FOREIGN KEY(content_id,org_id) REFERENCES social_contents(id,org_id),
 FOREIGN KEY(semantic_run_id,org_id) REFERENCES social_production_runs(id,org_id));
CREATE TABLE social_publish_intents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, content_id uuid NOT NULL,
 revision integer NOT NULL, connection_id uuid NOT NULL, check_id uuid NOT NULL,
 visibility text NOT NULL CHECK(visibility IN ('public','private')), owner_id uuid NOT NULL,
 credential_version integer NOT NULL, approval_hash text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'awaiting_confirmation' CHECK(state IN ('awaiting_confirmation','dispatching','submitted','verified','uncertain','failed','cancelled','invalidated')),
 approved_by uuid, operation_id uuid, receipt jsonb NOT NULL DEFAULT '{}', verification jsonb,
 error text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,org_id), UNIQUE(org_id,content_id,revision,connection_id),
 FOREIGN KEY(content_id,org_id) REFERENCES social_contents(id,org_id),
 FOREIGN KEY(connection_id,org_id) REFERENCES reach_connections(id,org_id),
 FOREIGN KEY(check_id,org_id) REFERENCES social_package_checks(id,org_id));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['social_media_assets','social_package_checks','social_publish_intents'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY scoped ON %I USING (reach_active_member(org_id)) WITH CHECK (reach_active_member(org_id))',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
GRANT UPDATE ON social_publish_intents TO everydayai,everydayai_runtime;
RESET ROLE;
