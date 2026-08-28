-- Roll back 160 core after all fixed Bundle facades have been removed.

DROP FUNCTION IF EXISTS _resolve_configuration_bundle(
    TEXT, TEXT, UUID, UUID
);
DROP FUNCTION IF EXISTS _resolve_effective_configuration_item(
    TEXT, TEXT, BOOLEAN, UUID, UUID
);
DROP FUNCTION IF EXISTS _project_configuration_entry(UUID, BOOLEAN);
DROP FUNCTION IF EXISTS _configuration_scope_id(TEXT, UUID, UUID);
DROP FUNCTION IF EXISTS get_configuration_bundle_registry_contract();
DROP TABLE IF EXISTS configuration_bundle_definitions;
