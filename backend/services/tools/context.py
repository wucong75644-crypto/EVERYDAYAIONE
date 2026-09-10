"""Request facts supplied by trusted server adapters, never model arguments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .spec import freeze


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    """No authentication occurs here. The caller must resolve identity and scope.

    authorized_tool_names is only an upper bound, NOT an execution grant.
    ToolAccessPolicy remains mandatory even when this set contains a name.
    Budget/cancellation are request-owned handles; all data snapshots are frozen.
    """

    actor_user_id: str
    workspace_owner_id: str
    org_id: str | None
    context_scope: str
    personal_context_allowed: bool
    agent_domain: str
    permission_mode: str
    execution_mode: str
    authorized_tool_names: frozenset[str] | None = None
    authorization_snapshot: Mapping[str, Any] = field(default_factory=dict)
    feature_flags: Mapping[str, bool] = field(default_factory=dict)
    resource_manifest: tuple[Mapping[str, Any], ...] | None = None
    resource_versions: Mapping[str, Any] = field(default_factory=dict)
    resource_access: Mapping[str, Any] = field(default_factory=dict)
    entrypoint: str = "model"
    conversation_id: str | None = None
    task_id: str | None = None
    call_id: str | None = None
    confirmation_available: bool = False
    budget: Any = field(default=None, repr=False, compare=False)
    cancellation: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.confirmation_available) is not bool:
            raise ValueError("confirmation_available must be a trusted boolean")
        for name in ("conversation_id", "task_id", "call_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Invalid trusted {name}")
        for field_name in ("actor_user_id", "workspace_owner_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Trusted {field_name} is required")
        if self.org_id is not None and (not isinstance(self.org_id, str) or not self.org_id.strip()):
            raise ValueError("org_id must be None or a nonempty trusted identifier")
        if self.context_scope not in {"user", "channel"}:
            raise ValueError("Invalid context_scope")
        if type(self.personal_context_allowed) is not bool:
            raise ValueError("personal_context_allowed must be explicit")
        if self.context_scope == "user" and self.workspace_owner_id != self.actor_user_id:
            raise ValueError("User workspace must belong to the actor")
        if self.context_scope == "channel" and (
            self.personal_context_allowed or self.workspace_owner_id == self.actor_user_id
        ):
            raise ValueError("Channel workspace cannot use personal context/owner")
        if self.agent_domain not in {"general", "erp"}:
            raise ValueError("Invalid agent_domain")
        if self.permission_mode not in {"ask", "auto", "plan"}:
            raise ValueError("Invalid permission_mode")
        if self.execution_mode not in {"interactive", "scheduled", "preflight"}:
            raise ValueError("Invalid execution_mode")
        if self.entrypoint not in {"model", "legacy_internal"}:
            raise ValueError("Invalid entrypoint")
        if self.authorized_tool_names is not None:
            if isinstance(self.authorized_tool_names, str) or any(
                not isinstance(name, str) or not name.strip() for name in self.authorized_tool_names
            ):
                raise ValueError("Invalid authorized_tool_names")
            object.__setattr__(self, "authorized_tool_names", frozenset(self.authorized_tool_names))
        if any(type(enabled) is not bool for enabled in self.feature_flags.values()):
            raise ValueError("Feature flags must be boolean snapshots")
        for field_name in ("authorization_snapshot", "feature_flags", "resource_manifest", "resource_versions", "resource_access"):
            object.__setattr__(self, field_name, freeze(getattr(self, field_name)))
