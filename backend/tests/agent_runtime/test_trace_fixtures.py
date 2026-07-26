"""Required AR-07 scenario coverage contract."""

from tests.agent_runtime.trace_schema import load_trace_manifest


REQUIRED_SCENARIOS = {
    "ordinary text response",
    "multiple model steps",
    "synchronous readonly tool",
    "tool failure",
    "worker loses lease",
    "duplicate command",
    "duplicate runtime event",
    "runtime event reorder",
    "runtime event gap",
    "projection replay",
    "accepted and unknown action placeholder",
    "user cancellation races completion",
    "enterprise employee scope",
    "personal null organization scope",
    "scope conflict rejected",
    "disconnect recovery",
    "actor restart recovery",
}


def test_required_scenarios_are_exactly_present() -> None:
    scenarios = {bundle["scenario"] for bundle in load_trace_manifest()}

    assert scenarios == REQUIRED_SCENARIOS


def test_placeholder_does_not_claim_reconciliation_implementation() -> None:
    bundle = next(
        item for item in load_trace_manifest()
        if item["scenario"] == "accepted and unknown action placeholder"
    )

    assert bundle["expected"]["outcome"] == "unknown"
    assert bundle["expected"]["terminal_state"] is None
    assert bundle["expected"]["side_effects"] == []
