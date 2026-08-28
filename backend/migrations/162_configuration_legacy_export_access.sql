-- 162: Record the administrator-granted legacy export source access contract.
-- Prerequisite: deploy/grant-legacy-config-export-access.sh.

DO $$
BEGIN
    IF to_regclass('public.kuaimai_external_credentials') IS NULL
       OR NOT has_table_privilege(
           'everydayai_owner',
           'public.kuaimai_external_credentials',
           'SELECT'
       ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_OWNER_SOURCE_ACCESS_INVALID';
    END IF;
    IF has_table_privilege(
        'everydayai_config_import_reader',
        'public.kuaimai_external_credentials',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID';
    END IF;
END;
$$;
