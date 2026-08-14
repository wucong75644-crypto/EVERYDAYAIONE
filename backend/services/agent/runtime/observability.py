"""Runtime-owned metrics, health and alert contracts.

Only bounded, redacted in-memory implementations live here.  Adapters for
Prometheus, Sentry or an alert platform must consume these contracts without
changing Runtime facts or performing recovery actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.status import RuntimeStatusSnapshot, RuntimeStatusState


class ObservabilityContractError(RuntimeError):
    """Failure-closed metrics, health or alert contract error."""


class MetricType(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


_LABELS = frozenset({
    "tenant_scope", "provider", "executor", "state", "outcome", "environment",
})
_MAX_CARDINALITY = {
    "tenant_scope": 1000, "provider": 64, "executor": 128,
    "state": 32, "outcome": 32, "environment": 8,
}
_SENSITIVE_PARTS = (
    "secret", "token", "password", "credential", "api_key", "authorization",
    "cookie", "payload", "prompt", "content", "argument", "path", "stack",
    "request", "user", "run", "action",
)


@dataclass(frozen=True, kw_only=True)
class MetricDefinition:
    name: str
    metric_type: MetricType
    unit: str
    description: str
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.startswith("agent_runtime_"):
            raise ValueError("RUNTIME_METRIC_NAME_INVALID")
        if not set(self.labels) <= _LABELS:
            raise ValueError("RUNTIME_METRIC_LABEL_INVALID")


@dataclass(frozen=True, kw_only=True)
class MetricSample:
    name: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsSink(Protocol):
    def emit(self, sample: MetricSample) -> None: ...


def _definition(name: str, metric_type: MetricType, unit: str, description: str,
                *labels: str) -> MetricDefinition:
    return MetricDefinition(
        name=name, metric_type=metric_type, unit=unit,
        description=description, labels=tuple(labels),
    )


METRICS: tuple[MetricDefinition, ...] = (
    _definition("agent_runtime_worker_heartbeat_age", MetricType.GAUGE, "seconds", "Age of the latest worker heartbeat.", "tenant_scope", "environment"),
    _definition("agent_runtime_worker_readiness", MetricType.GAUGE, "boolean", "Whether a worker reports ready.", "executor", "environment"),
    _definition("agent_runtime_worker_draining", MetricType.GAUGE, "boolean", "Whether a worker is draining.", "executor", "environment"),
    _definition("agent_runtime_worker_lease_renewal_failure_total", MetricType.COUNTER, "events", "Worker lease renewal failures.", "executor", "environment"),
    _definition("agent_runtime_worker_claim_failure_total", MetricType.COUNTER, "events", "Worker claim failures.", "executor", "environment"),
    _definition("agent_runtime_worker_restart_recovery_total", MetricType.COUNTER, "events", "Worker restart or recovery cycles.", "executor", "environment"),
    _definition("agent_runtime_action_submitted_total", MetricType.COUNTER, "actions", "Actions submitted to the Runtime executor path.", "executor", "outcome", "environment"),
    _definition("agent_runtime_action_completed_total", MetricType.COUNTER, "actions", "Actions completed.", "executor", "environment"),
    _definition("agent_runtime_action_failed_total", MetricType.COUNTER, "actions", "Actions failed.", "executor", "environment"),
    _definition("agent_runtime_action_cancelled_total", MetricType.COUNTER, "actions", "Actions cancelled by the Runtime owner.", "executor", "environment"),
    _definition("agent_runtime_dispatch_gate_rejection_total", MetricType.COUNTER, "events", "Dispatch gate rejections.", "executor", "outcome", "environment"),
    _definition("agent_runtime_fencing_rejection_total", MetricType.COUNTER, "events", "Fencing or stale-version rejections.", "executor", "environment"),
    _definition("agent_runtime_execution_latency", MetricType.HISTOGRAM, "seconds", "Fenced execution latency.", "executor", "outcome", "environment"),
    _definition("agent_runtime_provider_submit_total", MetricType.COUNTER, "submissions", "Provider submission attempts.", "provider", "environment"),
    _definition("agent_runtime_provider_accepted_total", MetricType.COUNTER, "submissions", "Provider submissions accepted for reconciliation.", "provider", "environment"),
    _definition("agent_runtime_provider_unknown_total", MetricType.COUNTER, "submissions", "Provider submissions with uncertain outcome.", "provider", "environment"),
    _definition("agent_runtime_provider_reconcile_total", MetricType.COUNTER, "reconciliations", "Provider readback or reconciliation attempts.", "provider", "outcome", "environment"),
    _definition("agent_runtime_provider_reconcile_age", MetricType.GAUGE, "seconds", "Age of the oldest provider reconciliation.", "provider", "state", "environment"),
    _definition("agent_runtime_provider_idempotency_conflict_total", MetricType.COUNTER, "events", "Duplicate or conflicting idempotency keys.", "provider", "environment"),
    _definition("agent_runtime_provider_revision_mismatch_total", MetricType.COUNTER, "events", "Provider revision fence failures.", "provider", "environment"),
    _definition("agent_runtime_projection_backlog", MetricType.GAUGE, "items", "Pending projection items.", "tenant_scope", "environment"),
    _definition("agent_runtime_projection_dead", MetricType.GAUGE, "items", "Dead projection items.", "tenant_scope", "environment"),
    _definition("agent_runtime_projection_oldest_age", MetricType.GAUGE, "seconds", "Age of the oldest pending projection.", "tenant_scope", "environment"),
    _definition("agent_runtime_projection_requeue_total", MetricType.COUNTER, "events", "Audited projection requeue results.", "outcome", "environment"),
    _definition("agent_runtime_scheduler_cas_conflict_total", MetricType.COUNTER, "events", "Scheduler CAS conflicts.", "tenant_scope", "environment"),
    _definition("agent_runtime_scheduler_stale_lease_total", MetricType.COUNTER, "events", "Scheduler stale leases.", "tenant_scope", "environment"),
    _definition("agent_runtime_scheduler_recovery_total", MetricType.COUNTER, "events", "Scheduler recovery operations.", "tenant_scope", "outcome", "environment"),
    _definition("agent_runtime_scheduler_fencing_rejection_total", MetricType.COUNTER, "events", "Scheduler fencing rejections.", "tenant_scope", "environment"),
    _definition("agent_runtime_artifact_readback_failure_total", MetricType.COUNTER, "events", "Artifact verified-readback failures.", "tenant_scope", "environment"),
    _definition("agent_runtime_artifact_orphan_count", MetricType.GAUGE, "items", "Orphan artifacts awaiting cleanup.", "tenant_scope", "environment"),
    _definition("agent_runtime_artifact_cleanup_failure_total", MetricType.COUNTER, "events", "Artifact cleanup failures.", "tenant_scope", "environment"),
    _definition("agent_runtime_workspace_recovery_total", MetricType.COUNTER, "events", "Workspace recovery operations.", "tenant_scope", "outcome", "environment"),
    _definition("agent_runtime_sandbox_residue_count", MetricType.GAUGE, "items", "Sandbox residues awaiting cleanup.", "environment"),
    _definition("agent_runtime_sandbox_cleanup_failure_total", MetricType.COUNTER, "events", "Sandbox cleanup failures.", "environment"),
    _definition("agent_runtime_sandbox_quarantine_count", MetricType.GAUGE, "items", "Sandbox jobs in quarantine.", "environment"),
    _definition("agent_runtime_cost_reserve_total", MetricType.COUNTER, "events", "Cost reservations.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_settle_total", MetricType.COUNTER, "events", "Cost settlements.", "tenant_scope", "outcome", "environment"),
    _definition("agent_runtime_cost_release_total", MetricType.COUNTER, "events", "Cost releases.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_refund_total", MetricType.COUNTER, "events", "Cost refunds.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_settlement_mismatch_total", MetricType.COUNTER, "events", "Cost settlement mismatches.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_late_settlement_total", MetricType.COUNTER, "events", "Late cost settlements.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_ledger_backlog", MetricType.GAUGE, "items", "Cost facts awaiting terminal settlement.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_terminal_without_settlement_total", MetricType.GAUGE, "items", "Terminal actions without a cost fact.", "tenant_scope", "environment"),
    _definition("agent_runtime_cost_refund_overflow_total", MetricType.GAUGE, "items", "Refunds exceeding settled amount.", "tenant_scope", "environment"),
    _definition("agent_runtime_provider_receipt_missing_total", MetricType.GAUGE, "items", "Provider submissions without a receipt.", "tenant_scope", "environment"),
    _definition("agent_runtime_provider_receipt_hash_mismatch_total", MetricType.GAUGE, "items", "Provider receipt hash mismatches.", "tenant_scope", "environment"),
    _definition("agent_runtime_external_duplicate_side_effect_rejection_total", MetricType.COUNTER, "events", "Rejected duplicate external side effects.", "tenant_scope", "environment"),
    _definition("agent_runtime_external_orphan_side_effect_total", MetricType.GAUGE, "items", "External side effects without a linked attempt.", "tenant_scope", "environment"),
    _definition("agent_runtime_external_late_settlement_total", MetricType.GAUGE, "items", "Late settlement observations.", "tenant_scope", "environment"),
    _definition("agent_runtime_external_reconcile_retry_age", MetricType.GAUGE, "seconds", "Age of the oldest reconcile-only side effect.", "tenant_scope", "environment"),
    _definition("agent_runtime_tenant_kill_switch_active", MetricType.GAUGE, "boolean", "Tenant kill switch state.", "tenant_scope", "environment"),
    _definition("agent_runtime_owner_runtime_owned", MetricType.GAUGE, "items", "Prepared tasks owned by Runtime.", "tenant_scope", "environment"),
    _definition("agent_runtime_owner_legacy_fallback", MetricType.GAUGE, "items", "Prepared tasks restored to the legacy actor.", "tenant_scope", "environment"),
    _definition("agent_runtime_owner_gate_blocked", MetricType.GAUGE, "items", "Owner transitions blocked by an ingress gate.", "tenant_scope", "environment"),
)
METRIC_CATALOG = {item.name: item for item in METRICS}


class InMemoryMetricsSink:
    """Bounded disposable sink; never reports production readiness."""

    production_ready = False

    def __init__(self, *, max_series: int = 2000) -> None:
        if max_series <= 0:
            raise ValueError("RUNTIME_METRIC_SERIES_LIMIT_INVALID")
        self._max_series = max_series
        self._samples: list[MetricSample] = []
        self._series: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        self._label_values: dict[str, set[str]] = {name: set() for name in _LABELS}

    def emit(self, sample: MetricSample) -> None:
        definition = METRIC_CATALOG.get(sample.name)
        if definition is None:
            raise ObservabilityContractError("RUNTIME_METRIC_UNKNOWN")
        labels = _validate_labels(sample.labels, definition)
        if not isinstance(sample.value, (int, float)) or sample.value != sample.value:
            raise ObservabilityContractError("RUNTIME_METRIC_VALUE_INVALID")
        series = (sample.name, tuple(sorted(labels.items())))
        if series not in self._series and len(self._series) >= self._max_series:
            raise ObservabilityContractError("RUNTIME_METRIC_CARDINALITY_LIMIT")
        for name, value in labels.items():
            values = self._label_values[name]
            if value not in values and len(values) >= _MAX_CARDINALITY[name]:
                raise ObservabilityContractError("RUNTIME_METRIC_CARDINALITY_LIMIT")
            values.add(value)
        self._series.add(series)
        self._samples.append(MetricSample(
            name=sample.name, value=float(sample.value), labels=labels,
            observed_at=sample.observed_at,
        ))

    def samples(self) -> tuple[MetricSample, ...]:
        return tuple(self._samples)


@dataclass(frozen=True, kw_only=True)
class HealthSnapshot:
    status: RuntimeStatusState
    component: str
    error_code: str | None
    observed_at: datetime
    contract_revision: str
    dependency_summary: Mapping[str, object]
    production_ready: bool
    environment: str

    def __post_init__(self) -> None:
        if not self.component or not self.contract_revision or not self.environment:
            raise ValueError("RUNTIME_HEALTH_IDENTITY_REQUIRED")
        if self.status is RuntimeStatusState.READY and self.error_code:
            raise ValueError("RUNTIME_HEALTH_READY_ERROR_CONFLICT")
        object.__setattr__(self, "dependency_summary", _safe_values(self.dependency_summary))

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value, "component": self.component,
            "error_code": self.error_code, "observed_at": self.observed_at.isoformat(),
            "contract_revision": self.contract_revision,
            "dependency_summary": dict(self.dependency_summary),
            "production_ready": self.production_ready,
            "environment": self.environment,
        }


def build_health_snapshot(
    *, component: str, status: RuntimeStatusState, error_code: str | None,
    dependency_summary: Mapping[str, object], production_ready: bool,
    environment: str, observed_at: datetime | None = None,
    contract_revision: str = "runtime-health-v1",
) -> HealthSnapshot:
    if status is RuntimeStatusState.READY and not production_ready:
        status = RuntimeStatusState.DEGRADED
        error_code = error_code or "PRODUCTION_READINESS_DISABLED"
    if status is not RuntimeStatusState.READY and not error_code:
        error_code = "RUNTIME_HEALTH_NOT_READY"
    return HealthSnapshot(
        status=status, component=component, error_code=error_code,
        observed_at=observed_at or datetime.now(timezone.utc),
        contract_revision=contract_revision,
        dependency_summary=dependency_summary,
        production_ready=production_ready, environment=environment,
    )


def build_runtime_health_snapshot(
    snapshot: RuntimeStatusSnapshot, *, environment: str,
    observed_at: datetime | None = None,
) -> HealthSnapshot:
    """Project a read-only status snapshot into the health contract."""
    states = [
        snapshot.composition, snapshot.workers, snapshot.tenant_control,
        snapshot.owner_transition,
    ]
    states.extend((snapshot.provider, snapshot.scheduler, snapshot.projection))
    status = RuntimeStatusState.READY
    error_code: str | None = None
    if any(item.state is RuntimeStatusState.UNAVAILABLE for item in states):
        status, error_code = RuntimeStatusState.UNAVAILABLE, "RUNTIME_STATUS_DOMAIN_UNAVAILABLE"
    elif any(item.state is RuntimeStatusState.DEGRADED for item in states):
        status, error_code = RuntimeStatusState.DEGRADED, "RUNTIME_STATUS_DEGRADED"
    elif snapshot.production.state is RuntimeStatusState.DISABLED:
        status, error_code = RuntimeStatusState.DISABLED, "PRODUCTION_READINESS_DISABLED"
    return build_health_snapshot(
        component="agent-runtime", status=status, error_code=error_code,
        dependency_summary={"state": status.value, "production_ready": False},
        production_ready=False, environment=environment, observed_at=observed_at,
    )


def emit_runtime_status_metrics(
    snapshot: RuntimeStatusSnapshot, sink: MetricsSink, *,
    tenant_scope: str, environment: str,
) -> None:
    """Emit bounded metrics from an already-authorized snapshot only."""
    labels = {"tenant_scope": tenant_scope, "environment": environment}
    provider_labels = {"provider": "aggregate", "environment": environment}
    _emit_snapshot_metric(sink, "agent_runtime_tenant_kill_switch_active",
                          bool(snapshot.tenant_control.error_code), labels)
    for key, metric in (
        ("runtime_owned", "agent_runtime_owner_runtime_owned"),
        ("legacy_fallback", "agent_runtime_owner_legacy_fallback"),
        ("gate_blocked", "agent_runtime_owner_gate_blocked"),
    ):
        _emit_domain_metric(sink, snapshot.owner_transition, metric, key, labels)
    _emit_domain_metric(sink, snapshot.submissions, "agent_runtime_provider_unknown_total",
                        "unknown", provider_labels)
    _emit_domain_metric(sink, snapshot.submissions, "agent_runtime_provider_accepted_total",
                        "accepted", provider_labels)
    _emit_domain_metric(sink, snapshot.submissions, "agent_runtime_provider_reconcile_age",
                        "reconcile_age_seconds", provider_labels)
    _emit_domain_metric(sink, snapshot.scheduler, "agent_runtime_scheduler_cas_conflict_total",
                        "cas_conflicts", labels)
    _emit_domain_metric(sink, snapshot.projection, "agent_runtime_projection_backlog",
                        "backlog", labels)
    _emit_domain_metric(sink, snapshot.projection, "agent_runtime_projection_dead",
                        "dead", labels)
    _emit_domain_metric(sink, snapshot.sandbox, "agent_runtime_sandbox_residue_count",
                        "residue_count", {"environment": environment})
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_cost_ledger_backlog",
                        "settlement_pending_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_cost_terminal_without_settlement_total",
                        "terminal_without_cost_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_cost_refund_overflow_total",
                        "refund_overflow_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_provider_receipt_missing_total",
                        "provider_without_readback_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_provider_receipt_hash_mismatch_total",
                        "receipt_hash_mismatch_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_external_duplicate_side_effect_rejection_total",
                        "duplicate_side_effect_rejection_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_external_orphan_side_effect_total",
                        "orphan_side_effect_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_external_late_settlement_total",
                        "late_settlement_count", labels)
    _emit_domain_metric(sink, snapshot.cost, "agent_runtime_external_reconcile_retry_age",
                        "reconcile_retry_age_seconds", labels)


def _emit_snapshot_metric(
    sink: MetricsSink, name: str, value: object, labels: Mapping[str, str],
) -> None:
    if isinstance(value, bool | int | float):
        sink.emit(MetricSample(name=name, value=float(value), labels=labels))


def _emit_domain_metric(
    sink: MetricsSink, domain: object, name: str, key: str,
    labels: Mapping[str, str],
) -> None:
    summary = getattr(domain, "summary", {})
    value = summary.get(key) if isinstance(summary, Mapping) else None
    _emit_snapshot_metric(sink, name, value, labels)


class AlertSeverity(StrEnum):
    WARNING = "warning"
    PAGE = "page"


@dataclass(frozen=True, kw_only=True)
class AlertRule:
    alert_id: str
    severity: AlertSeverity
    condition: str
    evaluation_window_seconds: int
    deduplication_key: tuple[str, ...]
    runbook_reference: str
    recommended_action: str
    auto_remediation_allowed: bool = False
    manual_confirmation_allowed: bool = True

    def __post_init__(self) -> None:
        if self.evaluation_window_seconds <= 0 or not self.deduplication_key:
            raise ValueError("RUNTIME_ALERT_RULE_INVALID")
        if self.auto_remediation_allowed:
            raise ValueError("RUNTIME_ALERT_AUTOREMEDIATION_FORBIDDEN")
        if "resubmit" in self.recommended_action.lower():
            raise ValueError("RUNTIME_ALERT_RESUBMIT_FORBIDDEN")


@dataclass(frozen=True, kw_only=True)
class AlertEvent:
    alert_id: str
    severity: AlertSeverity
    observed_at: datetime
    deduplication_key: str
    summary: str
    runbook_reference: str
    recommended_action: str
    manual_confirmation_allowed: bool
    deduplication_window_seconds: int


class AlertSink(Protocol):
    def emit(self, event: AlertEvent) -> bool: ...


class InMemoryAlertSink:
    production_ready = False

    def __init__(self) -> None:
        self._last_seen: dict[str, datetime] = {}
        self._events: list[AlertEvent] = []

    def emit(self, event: AlertEvent) -> bool:
        previous = self._last_seen.get(event.deduplication_key)
        if previous is not None and (
            event.observed_at - previous
        ).total_seconds() < event.deduplication_window_seconds:
            return False
        self._last_seen[event.deduplication_key] = event.observed_at
        self._events.append(event)
        return True

    def events(self) -> tuple[AlertEvent, ...]:
        return tuple(self._events)


def emit_alert(
    sink: AlertSink, rule: AlertRule, *, labels: Mapping[str, str],
    summary: str, observed_at: datetime | None = None,
) -> bool:
    safe_labels = _validate_alert_labels(labels, rule)
    if any(part in summary.lower() for part in _SENSITIVE_PARTS):
        raise ObservabilityContractError("RUNTIME_ALERT_SUMMARY_SENSITIVE")
    key = rule.alert_id + ":" + ":".join(
        f"{name}={safe_labels.get(name, '')}" for name in rule.deduplication_key
    )
    return sink.emit(AlertEvent(
        alert_id=rule.alert_id, severity=rule.severity,
        observed_at=observed_at or datetime.now(timezone.utc),
        deduplication_key=key, summary=summary[:240],
        runbook_reference=rule.runbook_reference,
        recommended_action=rule.recommended_action,
        manual_confirmation_allowed=rule.manual_confirmation_allowed,
        deduplication_window_seconds=rule.evaluation_window_seconds,
    ))


ALERT_RULES: tuple[AlertRule, ...] = (
    AlertRule(alert_id="runtime.worker.heartbeat_stale", severity=AlertSeverity.PAGE, condition="heartbeat_age_seconds > 30", evaluation_window_seconds=60, deduplication_key=("tenant_scope", "executor"), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#health-readiness-management-state-and-alerts", recommended_action="Inspect worker health and close the claim gate if stale."),
    AlertRule(alert_id="runtime.worker.readiness_degraded", severity=AlertSeverity.PAGE, condition="readiness == false", evaluation_window_seconds=60, deduplication_key=("tenant_scope", "executor"), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#health-readiness-management-state-and-alerts", recommended_action="Inspect the dependency error code; do not enable the gate."),
    AlertRule(alert_id="runtime.projection.dead_or_aged", severity=AlertSeverity.PAGE, condition="dead > 0 or oldest_age_seconds > 900", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#health-readiness-management-state-and-alerts", recommended_action="Review the dead item and use the audited projection recovery path."),
    AlertRule(alert_id="runtime.provider.reconcile_age", severity=AlertSeverity.PAGE, condition="reconcile_age_seconds exceeds SLO", evaluation_window_seconds=300, deduplication_key=("tenant_scope", "provider"), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#acceptedunknown-external-effects", recommended_action="Read back and reconcile the provider fact; do not dispatch again."),
    AlertRule(alert_id="runtime.provider.duplicate_or_revision_conflict", severity=AlertSeverity.WARNING, condition="idempotency_conflict_total or revision_mismatch_total increases", evaluation_window_seconds=300, deduplication_key=("tenant_scope", "provider"), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#acceptedunknown-external-effects", recommended_action="Inspect the request hash and provider revision fence."),
    AlertRule(alert_id="runtime.scheduler.cas_conflict_spike", severity=AlertSeverity.WARNING, condition="cas_conflict_total exceeds baseline", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AR17_RUNTIME_STATUS_CONTRACT.md", recommended_action="Inspect stale leases and fencing evidence; do not force a version."),
    AlertRule(alert_id="runtime.artifact.cleanup_failure", severity=AlertSeverity.PAGE, condition="cleanup_failure_total > 0", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#sandbox-production-contract", recommended_action="Quarantine and inspect the failed cleanup; do not delete facts."),
    AlertRule(alert_id="runtime.sandbox.residue_deadline", severity=AlertSeverity.PAGE, condition="residue_count > 0 past cleanup deadline", evaluation_window_seconds=300, deduplication_key=("environment",), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#sandbox-production-contract", recommended_action="Close code execution and inspect quarantine cleanup."),
    AlertRule(alert_id="runtime.cost.settlement_mismatch", severity=AlertSeverity.PAGE, condition="settlement_mismatch_total > 0", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AR17_RUNTIME_STATUS_CONTRACT.md", recommended_action="Reconcile the cost fact and provider receipt before settlement changes."),
    AlertRule(alert_id="runtime.cost.receipt_or_refund_anomaly", severity=AlertSeverity.PAGE, condition="receipt_hash_mismatch_total or refund_overflow_total > 0", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AR17_RUNTIME_STATUS_CONTRACT.md", recommended_action="Review the cost and provider receipt facts; do not force settle or refund."),
    AlertRule(alert_id="runtime.external.duplicate_or_orphan", severity=AlertSeverity.WARNING, condition="duplicate_side_effect_rejection_total or orphan_side_effect_total increases", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#acceptedunknown-external-effects", recommended_action="Inspect idempotency and attempt linkage; do not dispatch again."),
    AlertRule(alert_id="runtime.external.late_settlement", severity=AlertSeverity.WARNING, condition="late_settlement_total > 0", evaluation_window_seconds=300, deduplication_key=("tenant_scope", "provider"), runbook_reference="AGENT_RUNTIME_PRODUCTION_RUNBOOK.md#acceptedunknown-external-effects", recommended_action="Read back the provider fact and perform manual cost review."),
    AlertRule(alert_id="runtime.credential_or_provider_revision_failure", severity=AlertSeverity.PAGE, condition="credential or provider revision readiness is false", evaluation_window_seconds=300, deduplication_key=("tenant_scope", "provider"), runbook_reference="AR17_RUNTIME_STATUS_CONTRACT.md", recommended_action="Keep the provider capability closed and inspect the binding revision."),
    AlertRule(alert_id="runtime.tenant.kill_switch_active", severity=AlertSeverity.WARNING, condition="tenant kill switch is active", evaluation_window_seconds=300, deduplication_key=("tenant_scope",), runbook_reference="AR17_RUNTIME_STATUS_CONTRACT.md", recommended_action="Confirm ingress and dispatch remain closed; preserve reconciliation."),
)
ALERT_RULE_CATALOG = {rule.alert_id: rule for rule in ALERT_RULES}


def _validate_labels(labels: Mapping[str, str], definition: MetricDefinition) -> dict[str, str]:
    if set(labels) - set(definition.labels):
        raise ObservabilityContractError("RUNTIME_METRIC_LABEL_NOT_ALLOWED")
    result: dict[str, str] = {}
    for name, value in labels.items():
        if name not in _LABELS or any(part in name.lower() for part in _SENSITIVE_PARTS):
            raise ObservabilityContractError("RUNTIME_METRIC_LABEL_SENSITIVE")
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ObservabilityContractError("RUNTIME_METRIC_LABEL_VALUE_INVALID")
        if any(part in value.lower() for part in _SENSITIVE_PARTS):
            raise ObservabilityContractError("RUNTIME_METRIC_LABEL_VALUE_SENSITIVE")
        result[name] = value
    return result


def _validate_alert_labels(labels: Mapping[str, str], rule: AlertRule) -> dict[str, str]:
    if set(labels) - set(_LABELS) or not set(rule.deduplication_key) <= set(_LABELS):
        raise ObservabilityContractError("RUNTIME_ALERT_LABEL_NOT_ALLOWED")
    result = _validate_labels(labels, MetricDefinition(
        name="agent_runtime_alert", metric_type=MetricType.GAUGE,
        unit="event", description="alert labels", labels=tuple(_LABELS),
    ))
    for name, value in result.items():
        if len({value}) > _MAX_CARDINALITY[name]:
            raise ObservabilityContractError("RUNTIME_ALERT_CARDINALITY_LIMIT")
    return result


def _safe_values(value: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "ready", "status", "state", "error_code", "age_seconds",
        "oldest_age_seconds", "production_ready", "backend_safe", "count",
    }
    result: dict[str, object] = {}
    for key, item in value.items():
        name = str(key)
        if name not in allowed or any(part in name.lower() for part in _SENSITIVE_PARTS):
            continue
        if isinstance(item, (bool, int, float)) or item is None:
            result[name] = item
        elif isinstance(item, str) and len(item) <= 200:
            result[name] = item
    return result


__all__ = [
    "ALERT_RULE_CATALOG", "ALERT_RULES", "AlertEvent", "AlertRule",
    "AlertSeverity", "HealthSnapshot", "InMemoryAlertSink",
    "InMemoryMetricsSink", "METRIC_CATALOG", "METRICS", "MetricDefinition",
    "MetricSample", "MetricType", "MetricsSink", "ObservabilityContractError",
    "build_health_snapshot", "emit_alert",
    "build_runtime_health_snapshot", "emit_runtime_status_metrics",
]
