from __future__ import annotations

import pytest

from services.agent.runtime.status import (
    RuntimeStatusError,
    RuntimeStatusSnapshot,
    RuntimeStatusState,
)


def _payload() -> dict[str, object]:
    return {
        "control": {
            "ingress_enabled": False,
            "command_claim_enabled": False,
            "action_dispatch_enabled": False,
            "production_enabled": False,
        },
        "workers": [
            {"ready": True, "draining": False, "worker_id": "hidden"},
        ],
        "projection": {"backlog": 2, "dead": 0, "oldest_at": "2026-08-03T00:00:00Z"},
        "unknown": {"unknown": 1, "action_attempts_total": 4},
        "production_ready": False,
    }


def test_snapshot_is_tenant_scoped_and_production_closed() -> None:
    snapshot = RuntimeStatusSnapshot.from_admin_payload(_payload(), tenant_id="org-a")
    output = snapshot.to_dict()

    assert output["tenant_id"] == "org-a"
    assert output["production"]["state"] == RuntimeStatusState.DISABLED
    assert output["production"]["summary"]["production_enabled"] is False
    assert output["claim_gate"]["error_code"] == "RUNTIME_CLAIM_GATE_CLOSED"
    assert output["submissions"]["summary"]["unknown"] == 1
    assert output["claim_gate"]["summary"]["production_flags"]["code_execute_enabled"] is False


def test_tenant_mismatch_fails_closed() -> None:
    payload = _payload() | {"tenant_id": "org-b"}
    with pytest.raises(RuntimeStatusError, match="TENANT_SCOPE_MISMATCH"):
        RuntimeStatusSnapshot.from_admin_payload(payload, tenant_id="org-a")


def test_missing_domains_are_unavailable_not_ready() -> None:
    snapshot = RuntimeStatusSnapshot.from_admin_payload(_payload(), tenant_id="org-a")

    for name in ("provider", "scheduler", "artifact", "workspace", "child_run", "cost", "sandbox"):
        assert snapshot.to_dict()[name]["state"] == RuntimeStatusState.UNAVAILABLE
    assert "PROVIDER_STATUS_UNAVAILABLE" in snapshot.failure_closed_reasons


def test_domain_payload_is_redacted_and_unknown_is_read_only() -> None:
    snapshot = RuntimeStatusSnapshot.from_admin_payload(
        _payload(),
        tenant_id="org-a",
        domain_payloads={
            "provider": {
                "state": "degraded",
                "summary": {
                    "accepted": 2,
                    "unknown": 1,
                    "request_body": "must-not-appear",
                    "credential_handle": "must-not-appear",
                    "oldest_age_seconds": 120,
                },
            },
        },
    )
    output = repr(snapshot.to_dict())

    assert "must-not-appear" not in output
    assert "credential_handle" not in output
    assert "request_body" not in output
    assert "resubmit" not in output
    assert snapshot.provider.summary["accepted"] == 2


def test_invalid_tenant_scope_is_rejected() -> None:
    with pytest.raises(RuntimeStatusError, match="TENANT_SCOPE_REQUIRED"):
        RuntimeStatusSnapshot.from_admin_payload(_payload(), tenant_id=" ")
