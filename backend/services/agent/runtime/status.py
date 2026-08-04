"""Read-only, redacted Runtime status snapshot.

This module is an internal status contract.  It consumes already-authorized
status payloads and never claims, renews, mutates, or retries Runtime work.
Domains without an approved read-only source remain explicitly unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class RuntimeStatusState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


_SAFE_KEYS = frozenset({
    "accepted", "action_attempts_total", "backlog", "backend_safe",
    "capability_enabled", "cas_conflicts", "cleanup_failed", "count",
    "dead", "draining", "error_code", "failed", "gate_enabled",
    "mismatch", "oldest_age_seconds", "oldest_at", "orphan", "ready",
    "reconcile_required", "reserved", "production_enabled",
    "production_flags", "production_ready", "release_revision", "settled",
    "stale_leases", "state", "total", "unknown", "unknown_age_seconds",
    "worker_count", "ingress_enabled", "command_claim_enabled",
    "action_dispatch_enabled", "safe_actions_enabled", "non_safe_actions_enabled",
    "code_execute_enabled", "projection_enabled",
    "authorization_recovery_enabled",
    "kill_epoch", "state_version", "owner_fence_count",
    "provider_kill_epoch", "capability_kill_epoch", "reconcile_age_seconds",
    "cleanup_count", "recovery_count", "settlement_mismatch",
})
_SENSITIVE_PARTS = (
    "secret", "token", "password", "credential", "api_key", "authorization",
    "cookie", "payload", "prompt", "content", "argument", "stack",
    "path", "stdout", "request",
)


class RuntimeStatusError(RuntimeError):
    """Failure-closed status contract error."""


@dataclass(frozen=True, kw_only=True)
class DomainStatus:
    state: RuntimeStatusState
    summary: Mapping[str, object] = field(default_factory=dict)
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _safe_summary(self.summary))
        if self.state is RuntimeStatusState.UNAVAILABLE and not self.error_code:
            raise ValueError("RUNTIME_STATUS_UNAVAILABLE_REASON_REQUIRED")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "state": self.state.value,
            "summary": dict(self.summary),
        }
        if self.error_code:
            result["error_code"] = self.error_code
        return result


@dataclass(frozen=True, kw_only=True)
class RuntimeStatusSnapshot:
    """Tenant-scoped, additive status view with no mutation affordances."""

    tenant_id: str
    composition: DomainStatus
    workers: DomainStatus
    tenant_control: DomainStatus
    claim_gate: DomainStatus
    production: DomainStatus
    provider: DomainStatus
    submissions: DomainStatus
    scheduler: DomainStatus
    artifact: DomainStatus
    workspace: DomainStatus
    child_run: DomainStatus
    projection: DomainStatus
    cost: DomainStatus
    sandbox: DomainStatus
    failure_closed_reasons: tuple[str, ...]
    schema_version: int = 1
    capabilities: Mapping[str, DomainStatus] = field(default_factory=dict)

    @classmethod
    def from_admin_payload(
        cls, payload: Mapping[str, object], *, tenant_id: str,
        domain_payloads: Mapping[str, Mapping[str, object]] | None = None,
    ) -> "RuntimeStatusSnapshot":
        tenant = _required_tenant(tenant_id)
        if not isinstance(payload, Mapping):
            raise RuntimeStatusError("RUNTIME_STATUS_PAYLOAD_INVALID")
        payload_tenant = payload.get("tenant_id")
        if payload_tenant is not None and str(payload_tenant) != tenant:
            raise RuntimeStatusError("RUNTIME_STATUS_TENANT_SCOPE_MISMATCH")

        control = _mapping(payload.get("control"))
        workers = _workers(payload.get("workers"))
        projection = _existing_domain(payload.get("projection"), "PROJECTION_STATUS_UNAVAILABLE")
        unknown = _existing_domain(payload.get("unknown"), "PROVIDER_STATUS_UNAVAILABLE")
        supplied = domain_payloads or {}
        domains = {
            name: _supplied_domain(
                supplied.get(name) or _mapping(payload.get(name)) or None, code,
            )
            for name, code in (
                ("provider", "PROVIDER_STATUS_UNAVAILABLE"),
                ("scheduler", "SCHEDULER_STATUS_UNAVAILABLE"),
                ("artifact", "ARTIFACT_STATUS_UNAVAILABLE"),
                ("workspace", "WORKSPACE_STATUS_UNAVAILABLE"),
                ("child_run", "CHILD_RUN_STATUS_UNAVAILABLE"),
                ("cost", "COST_STATUS_UNAVAILABLE"),
                ("sandbox", "SANDBOX_STATUS_UNAVAILABLE"),
            )
        }
        production = _production_status(control, payload)
        tenant_control = _tenant_control(control)
        claim_gate = _claim_gate(control)
        reasons = _failure_reasons(
            payload, control, production, claim_gate, workers, domains,
            _capabilities(payload, supplied),
        )
        return cls(
            tenant_id=tenant,
            composition=_composition(payload), workers=workers,
            tenant_control=tenant_control,
            claim_gate=claim_gate, production=production,
            provider=domains["provider"], submissions=unknown,
            scheduler=domains["scheduler"], artifact=domains["artifact"],
            workspace=domains["workspace"], child_run=domains["child_run"],
            projection=projection, cost=domains["cost"],
            sandbox=domains["sandbox"],
            failure_closed_reasons=tuple(dict.fromkeys(reasons)),
            capabilities=_capabilities(payload, supplied),
        )

    def to_dict(self) -> dict[str, object]:
        """Return only the stable, redacted read model."""
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "composition": self.composition.to_dict(),
            "workers": self.workers.to_dict(),
            "tenant_control": self.tenant_control.to_dict(),
            "claim_gate": self.claim_gate.to_dict(),
            "production": self.production.to_dict(),
            "provider": self.provider.to_dict(),
            "submissions": self.submissions.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "artifact": self.artifact.to_dict(),
            "workspace": self.workspace.to_dict(),
            "child_run": self.child_run.to_dict(),
            "projection": self.projection.to_dict(),
            "cost": self.cost.to_dict(),
            "sandbox": self.sandbox.to_dict(),
            "capabilities": {
                name: status.to_dict()
                for name, status in sorted(self.capabilities.items())
            },
            "failure_closed_reasons": list(self.failure_closed_reasons),
        }


def _composition(payload: Mapping[str, object]) -> DomainStatus:
    value = payload.get("composition")
    if not isinstance(value, Mapping):
        return _unavailable("COMPOSITION_STATUS_UNAVAILABLE")
    return _status_from_mapping(value)


def _workers(value: object) -> DomainStatus:
    if not isinstance(value, list):
        return _unavailable("WORKER_STATUS_UNAVAILABLE")
    safe_rows = [_safe_summary(row) for row in value if isinstance(row, Mapping)]
    ready = sum(bool(row.get("ready")) for row in safe_rows)
    draining = sum(bool(row.get("draining")) for row in safe_rows)
    return DomainStatus(
        state=RuntimeStatusState.READY if safe_rows else RuntimeStatusState.DEGRADED,
        summary={"worker_count": len(safe_rows), "ready": ready, "draining": draining},
    )


def _claim_gate(control: Mapping[str, object]) -> DomainStatus:
    flags = {key: bool(control.get(key, False)) for key in (
        "ingress_enabled", "command_claim_enabled", "action_dispatch_enabled",
        "safe_actions_enabled", "non_safe_actions_enabled", "code_execute_enabled",
        "projection_enabled", "authorization_recovery_enabled",
    )}
    enabled = flags["command_claim_enabled"] and flags["action_dispatch_enabled"]
    return DomainStatus(
        state=RuntimeStatusState.READY if enabled else RuntimeStatusState.DISABLED,
        summary={"gate_enabled": enabled, "production_flags": flags},
        error_code=None if enabled else "RUNTIME_CLAIM_GATE_CLOSED",
    )


def _tenant_control(control: Mapping[str, object]) -> DomainStatus:
    if not control:
        return _unavailable("RUNTIME_CONTROL_STATUS_UNAVAILABLE")
    summary = _safe_summary(control)
    blocked = bool(control.get("kill_switch_active"))
    return DomainStatus(
        state=RuntimeStatusState.DEGRADED if blocked else RuntimeStatusState.READY,
        summary=summary,
        error_code="RUNTIME_TENANT_KILL_SWITCH_ACTIVE" if blocked else None,
    )


def _production_status(control: Mapping[str, object], payload: Mapping[str, object]) -> DomainStatus:
    production_ready = payload.get("production_ready") is True
    enabled = control.get("production_enabled") is True and production_ready
    summary = {
        "production_enabled": enabled,
        "production_ready": production_ready,
        "backend_safe": payload.get("backend_safe") is True,
    }
    return DomainStatus(
        state=RuntimeStatusState.READY if enabled else RuntimeStatusState.DISABLED,
        summary=summary,
        error_code=None if enabled else "PRODUCTION_READINESS_DISABLED",
    )


def _existing_domain(value: object, unavailable_code: str) -> DomainStatus:
    if not isinstance(value, Mapping):
        return _unavailable(unavailable_code)
    return _status_from_mapping(value)


def _supplied_domain(value: Mapping[str, object] | None, unavailable_code: str) -> DomainStatus:
    if value is None:
        return _unavailable(unavailable_code)
    return _status_from_mapping(value)


def _capabilities(
    payload: Mapping[str, object],
    supplied: Mapping[str, Mapping[str, object]],
) -> dict[str, DomainStatus]:
    raw = supplied.get("capabilities") or payload.get("capabilities")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, DomainStatus] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            continue
        result[name] = _status_from_mapping(value)
    return result


def _status_from_mapping(value: Mapping[str, object]) -> DomainStatus:
    raw_state = str(value.get("state", "degraded"))
    try:
        state = RuntimeStatusState(raw_state)
    except ValueError:
        state = RuntimeStatusState.DEGRADED
    error = value.get("error_code")
    return DomainStatus(
        state=state, summary=value.get("summary", value),
        error_code=str(error) if error else None,
    )


def _unavailable(code: str) -> DomainStatus:
    return DomainStatus(
        state=RuntimeStatusState.UNAVAILABLE,
        summary={"backend_safe": False}, error_code=code,
    )


def _failure_reasons(
    payload: Mapping[str, object], control: Mapping[str, object],
    production: DomainStatus, claim_gate: DomainStatus, workers: DomainStatus,
    domains: Mapping[str, DomainStatus],
    capabilities: Mapping[str, DomainStatus],
) -> list[str]:
    reasons: list[str] = []
    if production.state is not RuntimeStatusState.READY:
        reasons.append(str(production.error_code or "PRODUCTION_READINESS_DISABLED"))
    if claim_gate.state is RuntimeStatusState.DISABLED:
        reasons.append("RUNTIME_CLAIM_GATE_CLOSED")
    if workers.state is RuntimeStatusState.UNAVAILABLE:
        reasons.append("WORKER_STATUS_UNAVAILABLE")
    for domain in domains.values():
        if domain.state is RuntimeStatusState.UNAVAILABLE and domain.error_code:
            reasons.append(domain.error_code)
    for capability in capabilities.values():
        if capability.state is RuntimeStatusState.UNAVAILABLE and capability.error_code:
            reasons.append(capability.error_code)
    if not payload.get("composition"):
        reasons.append("COMPOSITION_STATUS_UNAVAILABLE")
    if not control:
        reasons.append("RUNTIME_CONTROL_STATUS_UNAVAILABLE")
    return reasons


def _safe_summary(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for key, item in value.items():
        name = str(key)
        lowered = name.lower()
        if name not in _SAFE_KEYS or any(part in lowered for part in _SENSITIVE_PARTS):
            continue
        if name == "production_flags" and isinstance(item, Mapping):
            result[name] = {
                flag: bool(item.get(flag, False)) for flag in (
                    "ingress_enabled", "command_claim_enabled",
                    "action_dispatch_enabled", "safe_actions_enabled",
                    "non_safe_actions_enabled", "code_execute_enabled",
                    "projection_enabled", "authorization_recovery_enabled",
                )
            }
        elif isinstance(item, bool | int | float) or item is None:
            result[name] = item
        elif isinstance(item, str) and len(item) <= 200:
            result[name] = item
    return result


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _required_tenant(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeStatusError("RUNTIME_STATUS_TENANT_SCOPE_REQUIRED")
    return value.strip()


__all__ = ["DomainStatus", "RuntimeStatusError", "RuntimeStatusSnapshot", "RuntimeStatusState"]
