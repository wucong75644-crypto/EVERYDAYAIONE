-- 219: Verify the admin-granted column dependency of WeCom employee facades.
-- Prerequisite: deploy/grant-sync-wecom-employee-access.sh.

DO $verify$
DECLARE
    required_column TEXT;
BEGIN
    IF to_regrole('everydayai_owner') IS NULL
       OR to_regrole('everydayai_sync') IS NULL
       OR to_regclass('public.wecom_employees') IS NULL
       OR to_regprocedure(
           'public.sync_list_wecom_employees(uuid)'
       ) IS NULL THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_ACCESS_PREREQUISITE_MISSING';
    END IF;

    FOREACH required_column IN ARRAY ARRAY[
        'org_id', 'wecom_userid', 'name', 'status'
    ]
    LOOP
        IF NOT has_column_privilege(
            'everydayai_owner',
            'public.wecom_employees',
            required_column,
            'SELECT'
        ) THEN
            RAISE EXCEPTION
                'SYNC_WECOM_EMPLOYEE_OWNER_COLUMN_ACCESS_MISSING: %',
                required_column;
        END IF;
    END LOOP;

    IF has_any_column_privilege(
        'everydayai_sync',
        'public.wecom_employees',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_DIRECT_ACCESS_INVALID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.oid =
               'public.sync_list_wecom_employees(uuid)'::REGPROCEDURE
           AND procedure.prosecdef
           AND pg_get_userbyid(procedure.proowner) = 'everydayai_owner'
    ) THEN
        RAISE EXCEPTION 'SYNC_WECOM_EMPLOYEE_FACADE_OWNER_INVALID';
    END IF;
END
$verify$;
