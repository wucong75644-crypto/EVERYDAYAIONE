-- Retired acceptance fixtures must not remain enabled. Keep revisions/history.
-- Legacy production skill tables are owned by everydayai; newer installations
-- use everydayai_owner. Do not transfer ownership or assume a bypass-RLS role.
DO $$
DECLARE packages_owner text; assignments_owner text;
BEGIN
    SELECT pg_get_userbyid(relowner) INTO packages_owner FROM pg_class
        WHERE oid='public.skill_packages'::regclass;
    SELECT pg_get_userbyid(relowner) INTO assignments_owner FROM pg_class
        WHERE oid='public.skill_assignments'::regclass;
    IF packages_owner NOT IN ('everydayai','everydayai_owner')
       OR assignments_owner IS DISTINCT FROM packages_owner THEN
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

UPDATE public.skill_assignments SET enabled=false
WHERE enabled AND package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
    AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report'));

DROP POLICY skill_cleanup_300_update ON public.skill_assignments;
DROP POLICY skill_cleanup_300_read ON public.skill_assignments;
DROP POLICY skill_cleanup_300_packages ON public.skill_packages;
