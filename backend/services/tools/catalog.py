"""Definition-only catalog and fresh compatibility projections."""
from .definitions import build_specs
from .registry import ToolRegistry


def build_tool_catalog() -> ToolRegistry:
    """Fresh registry over immutable definitions, with no request or handler state."""
    specs = build_specs()
    from core.config import get_settings
    if getattr(get_settings(), "mcp_connectors_enabled", False) is True:
        from .mcp_allowlist import registered_specs
        specs += registered_specs()
    return ToolRegistry(specs)


def build_capability_catalog() -> ToolRegistry:
    """Stable capability mapping, including reviewed MCP specs while gated off."""
    from .mcp_allowlist import registered_specs
    return ToolRegistry((*build_specs(), *registered_specs()))


def definition_registry() -> ToolRegistry:
    """Definition projection source for old config factories and constants."""
    return ToolRegistry(build_specs())


def group_schemas(group: str) -> list[dict]:
    return [s.to_schema() for s in definition_registry().specs() if group in s.catalog_groups]


def validation_schemas(group: str) -> dict[str, dict]:
    return {s.name: s.to_legacy_validation_schema() for s in definition_registry().specs()
            if group in s.catalog_groups and s.legacy_validation_schema is not None}
