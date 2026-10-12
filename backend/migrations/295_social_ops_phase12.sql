-- Incremental research/editorial data; no publishing authority.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE social_profiles (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL,
 name text NOT NULL, current_revision integer NOT NULL DEFAULT 1,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,org_id));
CREATE TABLE social_profile_revisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL,
 profile_id uuid NOT NULL, revision integer NOT NULL, data jsonb NOT NULL, sha256 text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(profile_id,revision), UNIQUE(id,org_id),
 FOREIGN KEY(profile_id,org_id) REFERENCES social_profiles(id,org_id));
CREATE TABLE social_research_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, owner_id uuid NOT NULL,
 client_request_id uuid NOT NULL, connection_id uuid NOT NULL, profile_revision_id uuid NOT NULL,
 plan jsonb NOT NULL, status text NOT NULL DEFAULT 'queued', generation integer NOT NULL DEFAULT 1,
 next_eligible_at timestamptz NOT NULL DEFAULT now(), next_occurrence_at timestamptz,
 last_error text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(org_id,client_request_id), UNIQUE(id,org_id),
 FOREIGN KEY(connection_id,org_id) REFERENCES reach_connections(id,org_id),
 FOREIGN KEY(profile_revision_id,org_id) REFERENCES social_profile_revisions(id,org_id));
CREATE TABLE social_research_items (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, job_id uuid NOT NULL,
 generation integer NOT NULL, item_key text NOT NULL, action text NOT NULL, page integer NOT NULL DEFAULT 1,
 request jsonb NOT NULL, status text NOT NULL DEFAULT 'queued', attempts integer NOT NULL DEFAULT 0,
 next_eligible_at timestamptz NOT NULL DEFAULT now(), lease_token uuid, lease_expires_at timestamptz,
 result_meta jsonb NOT NULL DEFAULT '{}',
 error text NOT NULL DEFAULT '', UNIQUE(job_id,generation,item_key,action,page), UNIQUE(id,org_id),
 FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id));
CREATE INDEX social_research_due ON social_research_items(status,next_eligible_at);
CREATE TABLE social_source_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, job_id uuid NOT NULL, item_id uuid NOT NULL,
 generation integer NOT NULL, source_key text NOT NULL, source_url text NOT NULL, title text NOT NULL,
 body text NOT NULL, body_hash text NOT NULL, coverage jsonb NOT NULL, metrics jsonb NOT NULL DEFAULT '{}',
 adapter_version text NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(item_id,source_key), UNIQUE(id,org_id),
 FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id),
 FOREIGN KEY(item_id,org_id) REFERENCES social_research_items(id,org_id));
CREATE TABLE social_contents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, job_id uuid NOT NULL,
 current_revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,org_id), FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id));
CREATE TABLE social_content_revisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, content_id uuid NOT NULL,
 revision integer NOT NULL, data jsonb NOT NULL, manifest jsonb NOT NULL, checks jsonb NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(content_id,revision),
 FOREIGN KEY(content_id,org_id) REFERENCES social_contents(id,org_id));
CREATE TABLE social_production_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, owner_id uuid NOT NULL,
 job_id uuid NOT NULL, kind text NOT NULL, input jsonb NOT NULL, stages jsonb NOT NULL,
 status text NOT NULL DEFAULT 'queued', stage_index integer NOT NULL DEFAULT 0,
 outputs jsonb NOT NULL DEFAULT '[]', result jsonb, error text NOT NULL DEFAULT '',
 lease_token uuid, lease_expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,org_id),
 FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id));
CREATE TABLE social_topic_candidates (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, job_id uuid NOT NULL,
 run_id uuid NOT NULL, profile_revision_id uuid NOT NULL,
 revision integer NOT NULL DEFAULT 1, data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(job_id,org_id) REFERENCES social_research_jobs(id,org_id),
 FOREIGN KEY(profile_revision_id,org_id) REFERENCES social_profile_revisions(id,org_id),
 FOREIGN KEY(run_id,org_id) REFERENCES social_production_runs(id,org_id));
CREATE TABLE reach_access_budgets (
 account_key text PRIMARY KEY, daily_limit integer NOT NULL DEFAULT 100 CHECK(daily_limit>=2),
 min_interval_seconds integer NOT NULL DEFAULT 30 CHECK(min_interval_seconds>=1),
 allowed_start_hour integer NOT NULL DEFAULT 0 CHECK(allowed_start_hour BETWEEN 0 AND 23),
 allowed_end_hour integer NOT NULL DEFAULT 24 CHECK(allowed_end_hour BETWEEN 1 AND 24),
 max_comment_pages integer NOT NULL DEFAULT 3 CHECK(max_comment_pages BETWEEN 0 AND 100),
 timezone text NOT NULL DEFAULT 'Asia/Shanghai', day date NOT NULL DEFAULT CURRENT_DATE,
 consumed integer NOT NULL DEFAULT 0, next_allowed_at timestamptz NOT NULL DEFAULT now(),
 breaker text NOT NULL DEFAULT '', credential_version integer NOT NULL DEFAULT 0);
CREATE TABLE reach_access_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL, connection_id uuid NOT NULL,
 account_key text NOT NULL REFERENCES reach_access_budgets(account_key), action text NOT NULL,
 units integer NOT NULL, basis text NOT NULL, outcome text NOT NULL DEFAULT 'dispatched_or_unknown',
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(connection_id,org_id) REFERENCES reach_connections(id,org_id));
-- Budget keys are opaque platform/account hashes. Same account shares limits,
-- but no organization research/content is exposed through that key.
CREATE FUNCTION reach_budget_visible(p_key text, p_admin boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM reach_connections c WHERE c.status='active'
 AND public.reach_active_member(c.org_id,p_admin)
 AND encode(sha256(convert_to(c.platform||':'||c.account_id,'UTF8')),'hex')=p_key)
$$;
REVOKE ALL ON FUNCTION reach_budget_visible(text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reach_budget_visible(text,boolean) TO everydayai,everydayai_runtime;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['social_profiles','social_profile_revisions','social_research_jobs',
 'social_topic_candidates','social_research_items','social_source_snapshots','social_contents','social_content_revisions','social_production_runs','reach_access_attempts'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY scoped ON %I USING (reach_active_member(org_id)) WITH CHECK (reach_active_member(org_id))',t);
 EXECUTE format('GRANT SELECT,INSERT ON %I TO everydayai,everydayai_runtime',t);
 END LOOP;
END $$;
GRANT UPDATE ON social_topic_candidates,social_profiles,social_research_jobs,social_research_items,social_contents,social_production_runs,reach_access_attempts TO everydayai,everydayai_runtime;
ALTER TABLE reach_access_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE reach_access_budgets FORCE ROW LEVEL SECURITY;
CREATE POLICY budget_select ON reach_access_budgets FOR SELECT USING(reach_budget_visible(account_key));
CREATE POLICY budget_insert ON reach_access_budgets FOR INSERT WITH CHECK(reach_budget_visible(account_key));
CREATE POLICY budget_update ON reach_access_budgets FOR UPDATE USING(reach_budget_visible(account_key)) WITH CHECK(reach_budget_visible(account_key));
GRANT SELECT,INSERT,UPDATE ON reach_access_budgets TO everydayai,everydayai_runtime;
CREATE POLICY social_job_discovery ON social_research_jobs FOR SELECT USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE POLICY social_job_worker_pause ON social_research_jobs FOR UPDATE USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker') WITH CHECK(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE POLICY social_run_worker_pause ON social_production_runs FOR UPDATE USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker') WITH CHECK(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
CREATE POLICY social_run_discovery ON social_production_runs FOR SELECT USING(current_user='everydayai_owner' AND current_setting('app.access_kind',true)='worker');
-- Discovery only: per-item claims and writes still run as the original member
-- under organization RLS. This host query cannot read source bodies or credentials.
CREATE FUNCTION social_ops_due() RETURNS TABLE(org_id uuid,owner_id uuid,job_id uuid,run_id uuid)
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
 ((j.status IN ('queued','running','waiting_budget') AND j.next_eligible_at<=now()) OR
 (j.status IN ('completed','partial','failed') AND j.next_occurrence_at<=now()))
 ORDER BY j.next_eligible_at,j.created_at LIMIT 10) q
 UNION ALL
 SELECT p.org_id,p.owner_id,p.job_id,p.run_id FROM (SELECT r.org_id,r.owner_id,r.job_id,r.id AS run_id FROM social_production_runs r
 WHERE current_setting('app.access_kind',true)='worker' AND (r.status='queued' OR
 (r.status='running' AND r.lease_expires_at<now())) ORDER BY r.created_at LIMIT 10) p
$$;
REVOKE ALL ON FUNCTION social_ops_due() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION social_ops_due() TO everydayai;
RESET ROLE;
