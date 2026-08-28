SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS get_configuration_registry_contract();
DROP FUNCTION IF EXISTS _list_configuration_status(TEXT, UUID, UUID);
DROP FUNCTION IF EXISTS _disable_configuration_entry(
    TEXT, UUID, UUID, TEXT, BIGINT, UUID
);
DROP FUNCTION IF EXISTS _write_configuration_entry(
    TEXT, UUID, UUID, TEXT, TEXT, JSONB, JSONB, BIGINT, UUID
);
DROP FUNCTION IF EXISTS _validate_configuration_material(
    TEXT, TEXT, TEXT, JSONB, JSONB
);
DROP FUNCTION IF EXISTS _assert_configuration_key_scope(TEXT, TEXT);
DROP FUNCTION IF EXISTS _assert_user_configuration_actor();
DROP FUNCTION IF EXISTS _assert_platform_configuration_actor();

RESET ROLE;
