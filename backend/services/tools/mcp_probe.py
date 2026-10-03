"""Operator health probe for the fixed synthetic Connector. No tool execution."""
import asyncio
import json

from core.config import get_settings
from .mcp_allowlist import CONNECTOR_ID
from .mcp_boundary import MCPError, MCPTimeoutError
from .mcp_client import MCPClient, bounded_operation


async def probe():
    if get_settings().mcp_connectors_enabled is not True:
        return {"connector": CONNECTOR_ID, "state": "disabled"}
    # This only probes the fixed local test server. It is not an organization
    # connection test and never reads or impersonates a stored organization token.
    client = MCPClient(bearer_token="synthetic-probe-token")
    async def operation():
        async with client.session():
            await client.health()
            specs = await client.discover()
            return {"connector": CONNECTOR_ID, "state": "ready", "tools": [s.name for s in specs]}
    try:
        return await bounded_operation(operation())
    except (MCPError, MCPTimeoutError) as error:
        return {"connector": CONNECTOR_ID, "state": "error", "code": error.code}


if __name__ == "__main__":
    status = asyncio.run(probe())
    print(json.dumps(status))
    raise SystemExit(1 if status["state"] == "error" else 0)
