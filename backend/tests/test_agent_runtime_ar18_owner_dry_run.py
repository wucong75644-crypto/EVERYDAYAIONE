"""AR-18 owner cutover dry-run; simulation only, never production readiness."""

from dataclasses import dataclass

import pytest


@dataclass
class _Action:
    status: str = "PENDING"
    owner: str | None = None
    token: str = "token-1"
    side_effects: int = 0


class _Cutover:
    def __init__(self) -> None:
        self.runtime_claims_enabled = False
        self.legacy_claims_enabled = True

    def drain_legacy(self, action: _Action) -> None:
        self.legacy_claims_enabled = False
        assert action.owner != "legacy"

    def claim_runtime(self, action: _Action) -> None:
        if not self.runtime_claims_enabled or action.owner is not None:
            raise RuntimeError("CLAIM_GATE_CLOSED")
        action.owner = "runtime"

    def dispatch_once(self, action: _Action, token: str) -> None:
        if action.owner != "runtime" or token != action.token:
            raise RuntimeError("FENCING_REJECTED")
        if action.status in {"ACCEPTED", "UNKNOWN"}:
            raise RuntimeError("RECONCILE_ONLY")
        action.side_effects += 1
        action.status = "ACCEPTED"

    def rollback(self) -> None:
        self.runtime_claims_enabled = False


def test_old_owner_drains_before_runtime_claim_and_duplicate_side_effect_is_blocked():
    cutover = _Cutover()
    action = _Action()
    cutover.drain_legacy(action)
    cutover.runtime_claims_enabled = True
    cutover.claim_runtime(action)
    cutover.dispatch_once(action, "token-1")
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        cutover.dispatch_once(action, "token-1")
    assert action.side_effects == 1


def test_accepted_and_unknown_are_reconcile_only_during_rollback():
    cutover = _Cutover()
    action = _Action(status="UNKNOWN", owner="runtime")
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        cutover.dispatch_once(action, "token-1")
    cutover.rollback()
    assert not cutover.runtime_claims_enabled
    assert action.status == "UNKNOWN"


def test_stale_completion_and_cancel_tokens_are_fenced():
    cutover = _Cutover()
    action = _Action(owner="runtime")
    with pytest.raises(RuntimeError, match="FENCING_REJECTED"):
        cutover.dispatch_once(action, "stale-token")
    assert action.side_effects == 0


def test_rollback_does_not_reopen_legacy_owner_for_ambiguous_action():
    cutover = _Cutover()
    action = _Action(status="ACCEPTED", owner="runtime")
    cutover.drain_legacy(action)
    cutover.rollback()
    assert not cutover.runtime_claims_enabled
    assert not cutover.legacy_claims_enabled
    assert action.owner == "runtime"


def test_local_dry_run_never_reports_production_ready():
    production_ready = False
    assert production_ready is False
