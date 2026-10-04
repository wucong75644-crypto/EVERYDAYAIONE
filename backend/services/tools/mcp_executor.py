"""Controlled executor; ToolPolicy must approve before this boundary is entered."""
import asyncio

from .mcp_allowlist import CONNECTOR_ID, TOOLS
from .mcp_boundary import MCPError, normalize_result
from .mcp_client import MCPClient, bounded_operation
from .spec import thaw


class MCPExecutor:
    def __init__(self, context_provider, db_provider=None):
        self._context_provider = context_provider
        self._db_provider = db_provider

    async def execute(self, spec, arguments, call_id):
        context = self._context_provider(call_id)
        if context.feature_flags.get("mcp_connectors_enabled") is not True:
            raise MCPError("MCP_FEATURE_DISABLED")
        if context.feature_flags.get("mcp_connector_test_readonly_enabled") is not True:
            raise MCPError("MCP_CONNECTOR_DISABLED")
        reviewed = next((t for t in TOOLS.values() if t.name == spec.name), None)
        if reviewed is None or spec != reviewed.spec():
            raise MCPError("MCP_TOOL_NOT_ALLOWLISTED")
        if arguments != {"record_id": "sample"}:
            raise MCPError("MCP_ARGUMENTS_INVALID")
        if self._db_provider is None or not context.org_id or not context.actor_user_id:
            raise MCPError("MCP_CREDENTIAL_UNAVAILABLE")
        from .mcp_org import connector_is_enabled, record_connector_health, resolve_org_bearer_token
        db = self._db_provider()
        if not connector_is_enabled(
            db, context.org_id, actor_user_id=context.actor_user_id,
        ):
            raise MCPError("MCP_CONNECTOR_DISABLED")
        try:
            token = resolve_org_bearer_token(
                db, org_id=context.org_id, actor_user_id=context.actor_user_id,
            )
        except MCPError as error:
            await asyncio.to_thread(
                record_connector_health, db, org_id=context.org_id,
                actor_user_id=context.actor_user_id,
                status="error", error_code=error.code,
            )
            raise
        timeout = min(5.0, context.budget.remaining) if context.budget is not None else 5.0

        async def operation():
            client = MCPClient(CONNECTOR_ID, bearer_token=token)
            async with client.session():
                await client.health()
                discovered = await client.discover()
                if spec not in discovered:
                    raise MCPError("MCP_SCHEMA_NOT_REVIEWED")
                # Recheck immediately before the only remote tool call so an
                # administrator's disable takes effect while handshake work is
                # in flight.
                if not connector_is_enabled(
                    db, context.org_id, actor_user_id=context.actor_user_id,
                ):
                    raise MCPError("MCP_CONNECTOR_DISABLED")
                result = await client.request("tools/call", {"name": reviewed.remote_name,
                    "arguments": thaw(arguments), "_meta": {"invocation_id": call_id}})
                return normalize_result(result, redact_values=(token,))

        try:
            result = await bounded_operation(
                operation(), timeout=timeout, cancellation=context.cancellation,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                record_connector_health, db, org_id=context.org_id,
                actor_user_id=context.actor_user_id,
                status="error", error_code="MCP_CANCELLED",
            )
            raise
        except Exception as error:
            code = getattr(error, "code", None)
            if code == "MCP_CONNECTOR_DISABLED":
                raise
            allowed = {
                "MCP_AUTH_FAILED", "MCP_CREDENTIAL_UNAVAILABLE", "MCP_HEALTH_FAILED",
                "MCP_REMOTE_ERROR", "MCP_PROTOCOL_ERROR", "MCP_RESULT_INVALID",
                "MCP_SCHEMA_NOT_REVIEWED", "MCP_TIMEOUT", "MCP_TOOL_ERROR",
                "MCP_UNAVAILABLE",
            }
            await asyncio.to_thread(
                record_connector_health, db, org_id=context.org_id,
                actor_user_id=context.actor_user_id,
                status="error", error_code=code if code in allowed else "MCP_REMOTE_ERROR",
            )
            raise
        else:
            await asyncio.to_thread(
                record_connector_health, db, org_id=context.org_id,
                actor_user_id=context.actor_user_id,
                status="ready",
            )
            return result
