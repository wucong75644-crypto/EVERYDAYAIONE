-- 272: Allow only the MCP facade owner to use the protected configuration
-- helpers needed to store and resolve its organization-scoped credential.
SET LOCAL ROLE everydayai;

GRANT SELECT ON TABLE public.configuration_policies TO everydayai_owner;
GRANT EXECUTE ON FUNCTION public._validate_configuration_material(
    TEXT, TEXT, TEXT, JSONB, JSONB
), public._write_configuration_entry(
    TEXT, UUID, UUID, TEXT, TEXT, JSONB, JSONB, BIGINT, UUID
), public._configuration_scope_id(
    TEXT, UUID, UUID
), public._project_configuration_entry(
    UUID, BOOLEAN
), public._resolve_effective_configuration_item(
    TEXT, TEXT, BOOLEAN, UUID, UUID
), public._resolve_configuration_bundle(
    TEXT, TEXT, UUID, UUID
) TO everydayai_owner;

RESET ROLE;
