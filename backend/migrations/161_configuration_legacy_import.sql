-- 161: One-shot, all-or-nothing legacy configuration import capability.
-- Prerequisites: migrations 158-160 and tenant database roles.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE configuration_import_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id UUID NOT NULL,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    definition_version VARCHAR(32) NOT NULL,
    config_key VARCHAR(120) NOT NULL,
    imported_version BIGINT NOT NULL CHECK (imported_version = 1),
    source_kind VARCHAR(32) NOT NULL CHECK (source_kind = 'legacy_v1'),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (definition_version, config_key)
        REFERENCES configuration_definitions(definition_version, config_key)
        ON DELETE RESTRICT,
    UNIQUE (import_id, org_id, config_key),
    UNIQUE (org_id, config_key)
);

COMMENT ON TABLE configuration_import_audit_log IS
    'Secret-free durable audit of one-time legacy configuration imports';

ALTER TABLE configuration_import_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuration_import_audit_log FORCE ROW LEVEL SECURITY;

CREATE POLICY configuration_import_audit_owner_only
ON configuration_import_audit_log
TO everydayai_owner
USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');

CREATE OR REPLACE FUNCTION export_legacy_configuration_snapshot()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_config_import_reader'
       OR current_setting(
           'app.legacy_config_export', TRUE
       ) IS DISTINCT FROM 'read' THEN
        RAISE EXCEPTION 'CONFIG_EXPORT_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object(
        'organizations',
        (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'id', organization.id,
                'wecom_corp_id', organization.wecom_corp_id,
                'encrypt_key', organization.encrypt_key
            ) ORDER BY organization.id), '[]'::JSONB)
              FROM public.organizations organization
        ),
        'org_configs',
        (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'org_id', config.org_id,
                'config_key', config.config_key,
                'config_value_encrypted', config.config_value_encrypted
            ) ORDER BY config.org_id, config.config_key), '[]'::JSONB)
              FROM public.org_configs config
        ),
        'external_credentials',
        (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'org_id', credential.org_id,
                'source', credential.source,
                'status', credential.status,
                'kuaimai_company_id', credential.kuaimai_company_id,
                'censeid_cookie', credential.censeid_cookie,
                'cookie_full', credential.cookie_full
            ) ORDER BY credential.org_id, credential.source), '[]'::JSONB)
              FROM public.kuaimai_external_credentials credential
        )
    );
END;
$$;

CREATE OR REPLACE FUNCTION import_legacy_configuration_batch(
    p_import_id UUID,
    p_items JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_item JSONB;
    v_result JSONB;
    v_org_id UUID;
    v_definition_version TEXT;
    v_config_key TEXT;
    v_count INTEGER := 0;
BEGIN
    IF session_user <> 'everydayai_migrator'
       OR current_setting(
           'app.legacy_config_import', TRUE
       ) IS DISTINCT FROM 'apply' THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_AUTHORITY_DENIED'
            USING ERRCODE = '42501';
    END IF;
    IF p_import_id IS NULL
       OR jsonb_typeof(p_items) <> 'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_items) item
         GROUP BY item->>'org_id', item->>'config_key'
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'CONFIG_IMPORT_DUPLICATE_ITEM'
            USING ERRCODE = '22023';
    END IF;

    FOR v_item IN SELECT value FROM jsonb_array_elements(p_items)
    LOOP
        IF jsonb_typeof(v_item) <> 'object'
           OR (SELECT COUNT(*) FROM jsonb_object_keys(v_item)) <> 5
           OR NOT (v_item ?& ARRAY[
               'org_id', 'definition_version', 'config_key',
               'value_json', 'secret_envelope'
           ]) THEN
            RAISE EXCEPTION 'CONFIG_IMPORT_ITEM_INVALID'
                USING ERRCODE = '22023';
        END IF;
        v_org_id := (v_item->>'org_id')::UUID;
        v_definition_version := v_item->>'definition_version';
        v_config_key := v_item->>'config_key';
        IF NOT EXISTS (
            SELECT 1 FROM public.organizations WHERE id = v_org_id
        ) THEN
            RAISE EXCEPTION 'CONFIG_IMPORT_ORGANIZATION_MISSING'
                USING ERRCODE = '22023';
        END IF;

        v_result := public._write_configuration_entry(
            'organization', v_org_id, NULL,
            v_definition_version, v_config_key,
            NULLIF(v_item->'value_json', 'null'::JSONB),
            NULLIF(v_item->'secret_envelope', 'null'::JSONB),
            0, NULL
        );
        INSERT INTO public.configuration_import_audit_log(
            import_id, org_id, definition_version, config_key,
            imported_version, source_kind
        ) VALUES (
            p_import_id, v_org_id, v_definition_version, v_config_key,
            (v_result->>'version')::BIGINT, 'legacy_v1'
        );
        v_count := v_count + 1;
    END LOOP;
    RETURN jsonb_build_object(
        'import_id', p_import_id,
        'imported_count', v_count,
        'version', 1
    );
END;
$$;

REVOKE ALL ON TABLE configuration_import_audit_log
FROM PUBLIC, everydayai_migrator, everydayai_runtime,
    everydayai_wecom_runtime, everydayai_worker;

REVOKE ALL ON FUNCTION import_legacy_configuration_batch(UUID, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION import_legacy_configuration_batch(UUID, JSONB)
TO everydayai_migrator;

REVOKE ALL ON FUNCTION export_legacy_configuration_snapshot()
FROM PUBLIC, everydayai_migrator, everydayai_runtime,
    everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION export_legacy_configuration_snapshot()
TO everydayai_config_import_reader;

RESET ROLE;
