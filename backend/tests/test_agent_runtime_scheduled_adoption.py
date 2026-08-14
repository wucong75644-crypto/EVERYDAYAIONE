from services.agent.runtime.scheduled_adoption import (
    AdoptionCategory,
    build_adoption_report,
    build_runtime_adoption_batch,
    build_runtime_adoption_facts,
    classify_scheduled_task,
    rollback_is_allowed,
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


def _runtime_facts() -> dict:
    return {
        "agent_definition_id": "everydayai-default",
        "agent_definition_revision": "v6",
        "agent_definition_hash": "a" * 64,
        "catalog_revision": "b" * 64,
        "source_effective_toolset_hash": "c" * 64,
        "effective_toolset_hash": "d" * 64,
        "model_snapshot": {"model_id": "qwen3.5-plus", "provider": "dashscope", "revision": "v1"},
        "toolset_snapshot": {"tool_names": ["get_conversation_context"]},
        "scope_snapshot": {"scope_kind": "user", "scope_id": "user-1"},
        "channel": "web",
        "budget_snapshot": {"max_credits": 10, "retry_count": 1, "timeout_sec": 180},
        "provider_key": "scheduler",
        "capability_key": "scheduler.task.cas",
        "provider_revision": "v1",
        "capability_revision": "v1",
        "request_hash": "e" * 64,
    }


def test_adoption_facts_are_not_ordinary_execution_identity() -> None:
    facts = build_runtime_adoption_facts(_task("active"), _runtime_facts())
    payload = facts.as_payload()
    assert payload["task_semantics_hash"] == classify_scheduled_task(_task("active")).task_semantics_hash
    assert "source_run_id" not in payload
    assert "source_action_id" not in payload
    assert "source_attempt_id" not in payload


def test_batch_requires_exact_candidate_set_and_rejects_running() -> None:
    tasks = [_task("active"), _task("paused", "paused"), _task("error", "error")]
    facts = {str(task["id"]): _runtime_facts() for task in tasks}
    assert set(build_runtime_adoption_batch(tasks, facts)) == {"active", "paused", "error"}
    try:
        build_runtime_adoption_batch(tasks, {"active": _runtime_facts()})
    except ValueError as error:
        assert "runtime_adoption_batch_mismatch" in str(error)
    else:
        raise AssertionError("missing adoption facts must fail closed")
    try:
        build_runtime_adoption_facts(_task("running", "running"), _runtime_facts())
    except ValueError as error:
        assert "task_not_adoptable" in str(error)
    else:
        raise AssertionError("running task must fail closed")


def test_rollback_is_fail_closed_after_side_effects() -> None:
    assert rollback_is_allowed(runtime_side_effects_exist=False) is True
    assert rollback_is_allowed(runtime_side_effects_exist=True) is False
