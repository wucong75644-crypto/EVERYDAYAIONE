"""Organization-scoped MCP activation and short-lived credential resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
    database_scope_from_client,
)
from .mcp_allowlist import CONNECTOR_ID
from .mcp_boundary import MCPError


def scoped_mcp_database(db: Any, *, org_id: str, actor_user_id: str) -> Any:
    """Bind an authenticated actor to one request-local MCP RPC transaction."""
    if not org_id or not actor_user_id:
        raise ValueError("MCP_CONNECTOR_SCOPE_REQUIRED")
    existing = database_scope_from_client(db)
    db_org_id = getattr(db, "org_id", existing.org_id if existing is not None else org_id)
    if db_org_id != org_id:
        raise ValueError("MCP_CONNECTOR_SCOPE_MISMATCH")
    if existing is not None and (
        existing.org_id != org_id or existing.actor_user_id != actor_user_id
    ):
        raise ValueError("MCP_CONNECTOR_SCOPE_MISMATCH")
    return ScopedDatabaseClient(db, DatabaseScope(
        actor_user_id=actor_user_id,
        org_id=org_id,
        access_kind=DatabaseAccessKind.RUNTIME,
    ))


def read_connector_state(
    db: Any, org_id: str, *, actor_user_id: str,
) -> dict[str, object] | None:
    """Read only the fixed connector's non-secret status; failures fail closed."""
    try:
        scoped_db = scoped_mcp_database(
            db, org_id=org_id, actor_user_id=actor_user_id,
        )
    except (TypeError, ValueError):
        return None
    try:
        response = scoped_db.rpc(
            "api_get_org_mcp_connector_state",
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


def connector_is_enabled(
    db: Any, org_id: str | None, *, actor_user_id: str | None,
) -> bool:
    if not org_id or not actor_user_id:
        return False
    state = read_connector_state(db, org_id, actor_user_id=actor_user_id)
    return state is not None and state.get("enabled") is True


def resolve_org_bearer_token(db: Any, *, org_id: str, actor_user_id: str) -> str:
    """Decrypt only the current organization's fixed test Connector Bundle."""
    if not org_id or not actor_user_id:
        raise MCPError("MCP_CREDENTIAL_UNAVAILABLE")
    try:
        from services.configuration.bundles import SecretBundleResolver
        from services.configuration.envelope import LocalKEKProvider
        from services.configuration.material_service import SecretMaterialService

        bundle = SecretBundleResolver(
            scoped_mcp_database(
                db, org_id=org_id, actor_user_id=actor_user_id,
            ),
            SecretMaterialService(LocalKEKProvider.from_environment()),
        ).mcp_test_readonly(
            actor_user_id=actor_user_id, org_id=org_id,
        )
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
    actor_user_id: str,
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
        scoped_mcp_database(
            db, org_id=org_id, actor_user_id=actor_user_id,
        ).rpc("api_record_org_mcp_connector_health", {
            "p_org_id": org_id,
            "p_connector_id": CONNECTOR_ID,
            "p_health_status": status,
            "p_error_code": error_code,
        }).execute()
        return True
    except Exception:
        return False
