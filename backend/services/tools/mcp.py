"""Connector identity/state contracts; fixed platform transport lives separately."""
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MCP_FEATURE_FLAG = "mcp_connectors_enabled"
ConnectorKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class MCPConnectorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    connector_id: ConnectorKey
    display_name: str = Field(min_length=1, max_length=200)
    # Operator-owned profile identity; never a URL, secret or arbitrary handler.
    profile_id: ConnectorKey


class MCPConnectorState(str, Enum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    READY = "ready"
    ERROR = "error"


def transition(state: MCPConnectorState, event: str, *, enabled: bool = False) -> MCPConnectorState:
    """Pure state machine; health facts originate at the controlled executor."""
    state = MCPConnectorState(state)
    if event == "disable":
        return MCPConnectorState.DISABLED
    if not enabled:
        raise ValueError("MCP_FEATURE_DISABLED")
    if event == "configure" and state in {MCPConnectorState.DISABLED, MCPConnectorState.ERROR}:
        return MCPConnectorState.CONFIGURED
    if event == "healthy" and state in {MCPConnectorState.CONFIGURED, MCPConnectorState.READY}:
        return MCPConnectorState.READY
    if event == "fail" and state in {MCPConnectorState.CONFIGURED, MCPConnectorState.READY}:
        return MCPConnectorState.ERROR
    raise ValueError("MCP_TRANSITION_NOT_SUPPORTED")
