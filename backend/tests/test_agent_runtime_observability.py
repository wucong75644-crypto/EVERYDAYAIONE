from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.observability import (
    ALERT_RULE_CATALOG,
    METRIC_CATALOG,
    AlertSeverity,
    InMemoryAlertSink,
    InMemoryMetricsSink,
    MetricSample,
    MetricType,
    ObservabilityContractError,
    build_health_snapshot,
    build_runtime_health_snapshot,
    emit_runtime_status_metrics,
    emit_alert,
)
from services.agent.runtime.status import RuntimeStatusState
from services.agent.runtime.status import RuntimeStatusSnapshot


def test_metric_catalog_is_stable_and_covers_all_domains() -> None:
    assert len(METRIC_CATALOG) >= 40
    assert all(name.startswith("agent_runtime_") for name in METRIC_CATALOG)
    assert METRIC_CATALOG["agent_runtime_execution_latency"].metric_type is MetricType.HISTOGRAM
    for prefix in ("worker", "action", "provider", "projection", "scheduler", "artifact", "workspace", "sandbox", "cost"):
        assert any(name.startswith(f"agent_runtime_{prefix}_") for name in METRIC_CATALOG)


def test_metric_labels_are_low_cardinality_and_redacted() -> None:
    sink = InMemoryMetricsSink(max_series=1)
    sink.emit(MetricSample(
        name="agent_runtime_provider_unknown_total", value=1,
        labels={"provider": "isolated", "environment": "ci"},
    ))
    with pytest.raises(ObservabilityContractError, match="LABEL_NOT_ALLOWED"):
        sink.emit(MetricSample(
            name="agent_runtime_provider_unknown_total", value=1,
            labels={"run_id": "run-1", "provider": "isolated"},
        ))
    with pytest.raises(ObservabilityContractError, match="SENSITIVE"):
        sink.emit(MetricSample(
            name="agent_runtime_provider_unknown_total", value=1,
            labels={"provider": "api_key-value"},
        ))
    with pytest.raises(ObservabilityContractError, match="CARDINALITY"):
        sink.emit(MetricSample(
            name="agent_runtime_provider_unknown_total", value=1,
            labels={"provider": "second"},
        ))


def test_health_never_reports_production_ready_from_local_profile() -> None:
    snapshot = build_health_snapshot(
        component="runtime", status=RuntimeStatusState.READY,
        error_code=None, dependency_summary={"ready": True, "path": "/secret"},
        production_ready=False, environment="ci",
    )
    output = snapshot.to_dict()

    assert output["status"] == RuntimeStatusState.DEGRADED
    assert output["production_ready"] is False
    assert output["error_code"] == "PRODUCTION_READINESS_DISABLED"
    assert "path" not in repr(output)


def test_health_unavailable_is_failure_closed_and_redacted() -> None:
    snapshot = build_health_snapshot(
        component="provider", status=RuntimeStatusState.UNAVAILABLE,
        error_code=None,
        dependency_summary={"status": "down", "error": "raw stack", "count": 0},
        production_ready=False, environment="ci",
    )
    output = snapshot.to_dict()

    assert output["status"] == RuntimeStatusState.UNAVAILABLE
    assert output["error_code"] == "RUNTIME_HEALTH_NOT_READY"
    assert "raw stack" not in repr(output)


def test_alert_rules_are_non_mutating_and_unknown_is_reconcile_only() -> None:
    assert len(ALERT_RULE_CATALOG) >= 11
    unknown_rule = ALERT_RULE_CATALOG["runtime.provider.reconcile_age"]
    assert unknown_rule.severity is AlertSeverity.PAGE
    assert unknown_rule.auto_remediation_allowed is False
    assert "reconcile" in unknown_rule.recommended_action.lower()
    assert "resubmit" not in unknown_rule.recommended_action.lower()


def test_alert_deduplication_uses_rule_window_and_safe_labels() -> None:
    sink = InMemoryAlertSink()
    rule = ALERT_RULE_CATALOG["runtime.provider.reconcile_age"]
    start = datetime(2026, 8, 3, tzinfo=timezone.utc)
    labels = {"tenant_scope": "tenant-hash", "provider": "isolated"}

    assert emit_alert(sink, rule, labels=labels, summary="reconcile age exceeded", observed_at=start)
    assert not emit_alert(sink, rule, labels=labels, summary="reconcile age exceeded", observed_at=start + timedelta(seconds=1))
    assert emit_alert(sink, rule, labels=labels, summary="reconcile age exceeded", observed_at=start + timedelta(seconds=301))
    assert all(event.manual_confirmation_allowed for event in sink.events())


def test_alert_payload_and_labels_cannot_contain_sensitive_values() -> None:
    sink = InMemoryAlertSink()
    rule = ALERT_RULE_CATALOG["runtime.tenant.kill_switch_active"]
    with pytest.raises(ObservabilityContractError, match="SENSITIVE"):
        emit_alert(sink, rule, labels={"tenant_scope": "tenant"}, summary="token=secret")
    with pytest.raises(ObservabilityContractError, match="LABEL_NOT_ALLOWED"):
        emit_alert(sink, rule, labels={"tenant_scope": "tenant", "user_id": "u"}, summary="switch active")


def test_status_projection_emits_only_read_metrics_and_disabled_health() -> None:
    snapshot = RuntimeStatusSnapshot.from_admin_payload(
        {"control": {"kill_switch_active": True, "production_enabled": False},
         "workers": [{"ready": False, "draining": False}],
         "production_ready": False,
         "projection": {"backlog": 2, "dead": 1},
         "unknown": {"unknown": 3, "accepted": 1, "reconcile_age_seconds": 42}},
        tenant_id="org-a",
    )
    sink = InMemoryMetricsSink()
    emit_runtime_status_metrics(snapshot, sink, tenant_scope="tenant-hash", environment="ci")
    names = {sample.name for sample in sink.samples()}
    assert "agent_runtime_tenant_kill_switch_active" in names
    assert "agent_runtime_provider_unknown_total" in names
    assert "agent_runtime_projection_dead" in names
    health = build_runtime_health_snapshot(snapshot, environment="ci")
    assert health.status in {RuntimeStatusState.UNAVAILABLE, RuntimeStatusState.DEGRADED}
    assert health.production_ready is False
