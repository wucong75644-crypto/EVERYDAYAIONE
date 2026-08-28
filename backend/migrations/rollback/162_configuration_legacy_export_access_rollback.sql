-- Run deploy/rollback-legacy-config-export-access.sh before this rollback.

DO $$
BEGIN
    IF to_regclass('public.kuaimai_external_credentials') IS NOT NULL
       AND has_table_privilege(
           'everydayai_owner',
           'public.kuaimai_external_credentials',
           'SELECT'
       ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_ACCESS_RUN_ADMIN_ROLLBACK_FIRST';
    END IF;
END;
$$;
