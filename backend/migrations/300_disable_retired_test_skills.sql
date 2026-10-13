-- Retired acceptance fixtures must not remain enabled. Keep revisions/history.
-- Legacy production skill tables are owned by everydayai; newer installations
-- use everydayai_owner. Do not transfer ownership or assume a bypass-RLS role.
DO $$
DECLARE packages_owner text; assignments_owner text; audits_owner text;
BEGIN
    SELECT pg_get_userbyid(relowner) INTO packages_owner FROM pg_class
        WHERE oid='public.skill_packages'::regclass;
    SELECT pg_get_userbyid(relowner) INTO assignments_owner FROM pg_class
        WHERE oid='public.skill_assignments'::regclass;
    SELECT pg_get_userbyid(relowner) INTO audits_owner FROM pg_class
        WHERE oid='public.skill_change_audits'::regclass;
    IF packages_owner NOT IN ('everydayai','everydayai_owner')
       OR assignments_owner IS DISTINCT FROM packages_owner
       OR audits_owner IS DISTINCT FROM packages_owner THEN
        RAISE EXCEPTION 'SKILL_CLEANUP_UNEXPECTED_TABLE_OWNER';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I', packages_owner);
END $$;

-- Narrow temporary policies also work with FORCE ROW LEVEL SECURITY.
CREATE POLICY skill_cleanup_300_packages ON public.skill_packages FOR SELECT TO CURRENT_USER
    USING (scope_kind='org' AND skill_key IN
        ('asset-test-0921','recommendation-smoke-20260926','test-daily-report'));
CREATE POLICY skill_cleanup_300_read ON public.skill_assignments FOR SELECT TO CURRENT_USER
    USING (package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
        AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report')));
CREATE POLICY skill_cleanup_300_update ON public.skill_assignments FOR UPDATE TO CURRENT_USER
    USING (package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
        AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report')))
    WITH CHECK (NOT enabled AND package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
        AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report')));

CREATE POLICY skill_cleanup_300_audit ON public.skill_change_audits FOR INSERT TO CURRENT_USER
    WITH CHECK (action='migration_300_disable_test_skills'
        AND org_id=public.skill_catalog_org_id() AND actor_user_id IS NULL
        AND package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
            AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report')));

-- Keep the normal audit triggers enabled, with an explicit migration identity
-- and the organization context expected by their guards and RLS policies.
DO $$
DECLARE target_org uuid;
BEGIN
    PERFORM set_config('app.access_kind','runtime_admin',true),
        set_config('app.actor_user_id','',true),
        set_config('app.request_id','migration-300',true),
        set_config('app.skill_action','migration_300_disable_test_skills',true);
    FOR target_org IN SELECT DISTINCT org_id FROM public.skill_assignments
        WHERE enabled AND package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
            AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report'))
    LOOP
        PERFORM set_config('app.org_id',target_org::text,true);
        UPDATE public.skill_assignments SET enabled=false
        WHERE org_id=target_org AND enabled AND package_id IN
            (SELECT id FROM public.skill_packages WHERE scope_kind='org'
                AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report'));
    END LOOP;
END $$;

DROP POLICY skill_cleanup_300_audit ON public.skill_change_audits;
DROP POLICY skill_cleanup_300_update ON public.skill_assignments;
DROP POLICY skill_cleanup_300_read ON public.skill_assignments;
DROP POLICY skill_cleanup_300_packages ON public.skill_packages;
