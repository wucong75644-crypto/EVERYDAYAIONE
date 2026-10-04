"""Platform-reviewed single Connector. No user URL, command or credential input."""
from dataclasses import dataclass
from types import MappingProxyType

from .spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec, freeze, thaw

CONNECTOR_ID = "test-readonly"
TOOL_NAME = "mcp_test_lookup"
REMOTE_TOOL_NAME = "lookup_sample"
INPUT_SCHEMA = freeze({
    "type": "object", "properties": {"record_id": {"type": "string", "enum": ["sample"]}},
    "required": ["record_id"], "additionalProperties": False,
})


@dataclass(frozen=True)
class ReviewedTool:
    remote_name: str
    name: str
    capability: str
    description: str
    reviewed_read_only_safe: bool = False

    def spec(self):
        return ToolSpec(
            name=self.name, capability=self.capability,
            schema={"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": thaw(INPUT_SCHEMA)}},
            domain="general", availability=ToolAvailability(feature_flags=(
                "mcp_connectors_enabled", "mcp_connector_test_readonly_enabled",
            )),
            risk_level="safe" if self.reviewed_read_only_safe is True else "confirm",
            parallelizable=False, cacheable=False, effects=("none",),
            executor_type="mcp", handler_key=f"{CONNECTOR_ID}:{self.remote_name}",
            exposure=Exposure.PUBLIC, source="platform.mcp_allowlist", definition_kind="explicit",
            replay_requirement="record_required", catalog_order=100,
            policy_rules=ToolPolicyRules(operation="read", plan_allowed=False,
                execution_modes=("interactive",)),
        )


# The only approved tool reads a constant synthetic record; no business access.
TOOLS = MappingProxyType({REMOTE_TOOL_NAME: ReviewedTool(
    REMOTE_TOOL_NAME, TOOL_NAME, "test.sample.read", "读取平台 MCP 测试 Connector 的合成 sample 记录。",
    reviewed_read_only_safe=True,
)})


def require_connector(connector_id):
    if connector_id != CONNECTOR_ID:
        raise ValueError("MCP_CONNECTOR_NOT_ALLOWLISTED")


def registered_specs():
    return tuple(tool.spec() for tool in TOOLS.values())
