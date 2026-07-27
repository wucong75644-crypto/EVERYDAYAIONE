-- The external admin grant is reverted separately by:
-- deploy/rollback-sync-wecom-employee-access.sh.
DO $verify$
BEGIN
    IF to_regclass('public.wecom_employees') IS NULL THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_ACCESS_PREREQUISITE_MISSING';
    END IF;
END
$verify$;
