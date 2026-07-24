SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.configuration_import_audit_log) THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_ROLLBACK_DATA_PRESENT';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION import_legacy_configuration_batch(UUID, JSONB)
FROM PUBLIC, everydayai_migrator, everydayai_runtime,
    everydayai_wecom_runtime, everydayai_worker;
DROP FUNCTION IF EXISTS import_legacy_configuration_batch(UUID, JSONB);

DROP FUNCTION IF EXISTS export_legacy_configuration_snapshot();

DROP TABLE IF EXISTS configuration_import_audit_log;

RESET ROLE;
