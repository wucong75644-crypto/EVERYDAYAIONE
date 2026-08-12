from services.agent.runtime.scheduled_adoption import (
    AdoptionCategory,
    build_adoption_report,
    classify_scheduled_task,
)


def _task(task_id: str, status: str = "active", **overrides):
    value = {
        "id": task_id,
        "org_id": "org-1",
        "user_id": "user-1",
        "name": "daily report",
        "prompt": "summarize orders",
        "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "user-1"},
        "max_credits": 10,
        "retry_count": 1,
        "timeout_sec": 180,
        "schedule_type": "cron",
        "cron_expr": "0 9 * * *",
        "next_run_at": "2026-08-13T01:00:00+00:00",
        "status": status,
    }
    value.update(overrides)
    return value


def test_profileless_active_task_is_candidate_but_never_safe() -> None:
    decision = classify_scheduled_task(_task("active"))
    assert decision.category == AdoptionCategory.CANDIDATE_RUNTIME_SOURCE_REQUIRED
    assert decision.adoption_candidate is True
    assert decision.safe_to_adopt is False
    assert "runtime_source_action_attempt_run_missing" in decision.reason_codes


def test_profile_and_unsafe_states_are_distinguished() -> None:
    assert classify_scheduled_task(
        _task("runtime"), profile_exists=True
    ).category == AdoptionCategory.RUNTIME_OWNED
    assert classify_scheduled_task(_task("running", "running")).category == AdoptionCategory.BLOCKED_RUNNING
    assert classify_scheduled_task(_task("paused", "paused")).category == AdoptionCategory.PRESERVE_PAUSED
    assert classify_scheduled_task(_task("error", "error")).category == AdoptionCategory.PRESERVE_ERROR


def test_partial_identity_and_invalid_task_fail_closed() -> None:
    partial = classify_scheduled_task(_task("partial", runtime_action_id="a"))
    assert partial.category == AdoptionCategory.BLOCKED_PARTIAL_RUNTIME_FACTS
    invalid = classify_scheduled_task(_task("invalid", push_target={"type": "unknown"}))
    assert invalid.category == AdoptionCategory.BLOCKED_INVALID_TASK


def test_report_is_deterministic_and_does_not_echo_prompt_or_target() -> None:
    report = build_adoption_report(
        [_task("b", prompt="private prompt"), _task("a")],
        profile_task_ids=["a"],
    )
    assert report["tasks"][0]["task_id"] == "a"
    assert report["counts"] == {
        "candidate_runtime_source_required": 1,
        "runtime_owned": 1,
    }
    rendered = str(report)
    assert "private prompt" not in rendered
    assert "push_target" not in rendered

