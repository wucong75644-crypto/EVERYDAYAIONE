"""Safe logical capability availability for Skill summaries and runtime ceilings."""


def available_capability_names(db, org_id, settings):
    """Return registered capability IDs available in this org's current scope.

    Only the fixed platform MCP Connector depends on organization activation.
    No credential, remote description, URL, or health detail enters this projection.
    """
    from services.tools.catalog import build_capability_catalog

    registry = build_capability_catalog()
    mcp_enabled = getattr(settings, "mcp_connectors_enabled", False) is True
    if mcp_enabled and org_id:
        from services.tools.mcp_org import connector_is_enabled
        mcp_enabled = connector_is_enabled(db, str(org_id))
    settings_flags = {
        name: getattr(settings, name, False) is True
        for name in type(settings).model_fields
    }
    settings_flags["mcp_connectors_enabled"] = mcp_enabled
    settings_flags["mcp_connector_test_readonly_enabled"] = mcp_enabled

    grouped = {}
    available = {}
    for spec in registry.specs():
        if not spec.capability:
            continue
        grouped.setdefault(spec.capability, set()).add(spec.name)
        if spec.availability.requires_org and not org_id:
            continue
        if spec.executor_type == "mcp" and not mcp_enabled:
            continue
        if any(settings_flags.get(flag) is not True for flag in spec.availability.feature_flags):
            continue
        available.setdefault(spec.capability, set()).add(spec.name)
    return frozenset(capability for capability, names in grouped.items()
                     if names and names <= available.get(capability, set()))


def available_tool_names(registry, context):
    """Ask the canonical ToolPolicy/Registry path for a conservative ceiling.

    This list only narrows Skill activation/checkpoints; every invocation is
    checked again by ToolPolicy at dispatch time.
    """
    from services.tools.policy import ToolPolicy

    policy = ToolPolicy(registry)
    return frozenset(
        spec.name for spec in registry.specs()
        if registry.check_access(spec.name, context, policy=policy).allowed
    )
