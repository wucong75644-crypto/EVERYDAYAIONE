-- 272 rollback: restore the configuration helper permissions to their
-- pre-MCP-facade state.
SET LOCAL ROLE everydayai;

REVOKE EXECUTE ON FUNCTION public._validate_configuration_material(
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
) FROM everydayai_owner;
REVOKE SELECT ON TABLE public.configuration_policies FROM everydayai_owner;

RESET ROLE;
