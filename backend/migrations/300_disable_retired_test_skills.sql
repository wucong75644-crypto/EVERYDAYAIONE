-- Retired acceptance fixtures must not remain enabled. Keep revisions/history.
SET LOCAL ROLE everydayai_owner;

-- Narrow temporary policies also work with FORCE ROW LEVEL SECURITY.
CREATE POLICY skill_cleanup_300_packages ON public.skill_packages FOR SELECT TO everydayai_owner
    USING (scope_kind='org' AND skill_key IN
        ('asset-test-0921','recommendation-smoke-20260926','test-daily-report'));
CREATE POLICY skill_cleanup_300_read ON public.skill_assignments FOR SELECT TO everydayai_owner
    USING (package_id IN (SELECT id FROM public.skill_packages WHERE scope_kind='org'
        AND skill_key IN ('asset-test-0921','recommendation-smoke-20260926','test-daily-report')));
CREATE POLICY skill_cleanup_300_update ON public.skill_assignments FOR UPDATE TO everydayai_owner
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
