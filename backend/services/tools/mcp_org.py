"""Organization-scoped MCP activation and short-lived credential resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.db_scope import database_scope_from_client
from .mcp_allowlist import CONNECTOR_ID
from .mcp_boundary import MCPError


def read_connector_state(db: Any, org_id: str) -> dict[str, object] | None:
    """Read only the fixed connector's non-secret status; failures fail closed."""
    scope = database_scope_from_client(db)
    db_org_id = getattr(db, "org_id", scope.org_id if scope is not None else org_id)
    if not org_id or db_org_id != org_id or (scope is not None and scope.org_id != org_id):
        return None
    try:
        response = db.rpc(
            "get_org_mcp_connector_state",
            {"p_org_id": org_id, "p_connector_id": CONNECTOR_ID},
        ).execute()
        data = response.data if response is not None else None
        if (not isinstance(data, dict) or data.get("org_id") != org_id
                or data.get("connector_id") != CONNECTOR_ID
                or type(data.get("enabled")) is not bool):
            return None
        return data
    except Exception:
        return None


def connector_is_enabled(db: Any, org_id: str | None) -> bool:
    if not org_id:
        return False
    state = read_connector_state(db, org_id)
    return state is not None and state.get("enabled") is True


def resolve_org_bearer_token(db: Any, *, org_id: str, actor_user_id: str) -> str:
    """Decrypt only the current organization's fixed test Connector Bundle."""
    scope = database_scope_from_client(db)
    db_org_id = getattr(db, "org_id", scope.org_id if scope is not None else org_id)
    if (not org_id or not actor_user_id or db_org_id != org_id
            or (scope is not None and (
                scope.org_id != org_id or scope.actor_user_id != actor_user_id
            ))):
        raise MCPError("MCP_CREDENTIAL_UNAVAILABLE")
    try:
        from services.configuration.bundles import SecretBundleResolver
        from services.configuration.envelope import LocalKEKProvider
        from services.configuration.material_service import SecretMaterialService

        bundle = SecretBundleResolver(
            db,
            SecretMaterialService(LocalKEKProvider.from_environment()),
        ).mcp_test_readonly()
        credential = bundle.values.get("mcp.test_readonly.bearer_token")
        token = credential.get("token") if isinstance(credential, Mapping) else None
        if not isinstance(token, str) or not token:
            raise MCPError("MCP_CREDENTIAL_UNAVAILABLE")
        return token
    except MCPError:
        raise
    except Exception:
        # Configuration, envelope, KEK, and database details must not enter
        # tool output or ordinary logs.
        raise MCPError("MCP_CREDENTIAL_UNAVAILABLE") from None


def record_connector_health(
    db: Any,
    *,
    org_id: str,
    status: str,
    error_code: str | None = None,
) -> bool:
    """Best-effort status projection with a closed, token-free error vocabulary."""
    allowed_errors = {
        "MCP_AUTH_FAILED", "MCP_CREDENTIAL_UNAVAILABLE", "MCP_HEALTH_FAILED",
        "MCP_REMOTE_ERROR", "MCP_PROTOCOL_ERROR", "MCP_RESULT_INVALID",
        "MCP_SCHEMA_NOT_REVIEWED", "MCP_TIMEOUT", "MCP_TOOL_ERROR",
        "MCP_UNAVAILABLE", "MCP_CANCELLED",
    }
    if status not in {"ready", "error"} or (error_code and error_code not in allowed_errors):
        return False
    try:
        db.rpc("record_org_mcp_connector_health", {
            "p_org_id": org_id,
            "p_connector_id": CONNECTOR_ID,
            "p_health_status": status,
            "p_error_code": error_code,
        }).execute()
        return True
    except Exception:
        return False
