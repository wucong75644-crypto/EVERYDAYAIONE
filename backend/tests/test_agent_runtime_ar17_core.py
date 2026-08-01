from __future__ import annotations

import pytest

from services.agent.runtime.agents import AgentDefinition
from services.agent.runtime.catalog import (
    EffectiveToolset, build_default_runtime_catalog,
)
from services.agent.runtime.catalog.types import RuntimeToolDefinition
from services.agent.runtime.context import build_runtime_context


def _definition() -> AgentDefinition:
    return AgentDefinition(
        canonical_key="everydayai-default", revision="v1",
        prompt_revision="agent-runtime-production-v1",
        requested_tool_groups=frozenset({"code"}),
    )


def _toolset() -> EffectiveToolset:
    catalog = build_default_runtime_catalog()
    return EffectiveToolset.build(
        agent=_definition(), catalog=catalog, scope="user", channel="web",
        entitled_groups=frozenset({"code"}),
        authorized_names=frozenset({"code_execute"}),
    )


def test_definition_and_catalog_hashes_are_deterministic() -> None:
    assert _definition().definition_hash == _definition().definition_hash
    left = build_default_runtime_catalog()
    right = build_default_runtime_catalog()
    assert left.revision == right.revision
    assert _toolset().toolset_hash == _toolset().toolset_hash


def test_effective_toolset_is_fail_closed_and_executor_backed() -> None:
    toolset = _toolset()
    assert [tool.canonical_name for tool in toolset.definitions] == ["code_execute"]
    with pytest.raises(ValueError, match="NOT_OFFERED"):
        toolset.validate_call("unknown_tool", {})
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        toolset.validate_call("code_execute", {"code": "print(1)"})
    toolset.validate_call("code_execute", {"code": "print(1)", "description": "test"})


def test_effective_toolset_validates_nested_schema_and_json_values() -> None:
    tool = RuntimeToolDefinition(
        canonical_name="nested", tool_group="code",
        schema={"type": "object", "required": ["mode", "items"],
                "additionalProperties": False,
                "properties": {"mode": {"type": "string", "enum": ["safe"]},
                                "items": {"type": "array", "items": {"type": "integer"}}}},
        safety_level="safe", executor_type="test", executor_revision=1,
        capability_requirements=frozenset(), side_effect="none",
        authorization_requirement="none", retry_semantics="safe",
        reconcile_semantics="none", cancel_semantics="none", result_schema_revision=1,
    )
    toolset = EffectiveToolset(definitions=(tool,), catalog_revision="catalog",
                               toolset_hash="toolset")
    toolset.validate_call("nested", {"mode": "safe", "items": [1, 2]})
    for invalid in (
        {"mode": "safe", "items": [1], "extra": True},
        {"mode": "unsafe", "items": [1]},
        {"mode": "safe", "items": [1.5]},
        {"mode": "safe", "items": [float("nan")]},
    ):
        with pytest.raises(ValueError, match="SCHEMA_INVALID"):
            toolset.validate_call("nested", invalid)


def test_context_is_anchor_bound_and_repeats_tool_call_and_result_once() -> None:
    toolset = _toolset()
    action = {
        "model_step_id": "step-1", "stable_tool_call_id": "call-1",
        "tool_name": "code_execute", "status": "completed", "arguments": {
            "code": "print(1)", "description": "test",
        }, "result": {"status": "success", "summary": "done",
                       "data": {"value": 1}, "external_receipt": "hidden"},
    }
    first = build_runtime_context(
        run={"id": "run-1", "context_receipt": {
            "base_context_revision": "message:1", "through_message_id": "msg-1",
        }}, session={"id": "session-1"}, messages=[
            {"role": "user", "content": "hello"},
        ], actions=[action], toolset=toolset, model_step=2,
    )
    second = build_runtime_context(
        run={"id": "run-1", "context_receipt": {
            "base_context_revision": "message:1", "through_message_id": "msg-1",
        }}, session={"id": "session-1"}, messages=[
            {"role": "user", "content": "hello"},
        ], actions=[action], toolset=toolset, model_step=2,
    )
    messages, _ = first.plan.project()
    assert first.plan.plan_hash == second.plan.plan_hash
    assert [message["role"] for message in messages] == [
        "user", "assistant", "tool",
    ]
    assert '"status":"success"' in messages[-1]["content"]
    assert "external_receipt" not in messages[-1]["content"]


def test_context_rejects_missing_anchor_and_unknown_action() -> None:
    with pytest.raises(RuntimeError, match="ANCHOR_MISSING"):
        build_runtime_context(
            run={"id": "run-1", "context_receipt": {}},
            session={"id": "session-1"}, messages=[], actions=[],
            toolset=_toolset(), model_step=1,
        )
    with pytest.raises(ValueError, match="NOT_OFFERED"):
        build_runtime_context(
            run={"id": "run-1", "context_receipt": {
                "base_context_revision": "message:1", "through_message_id": "msg-1",
            }}, session={"id": "session-1"}, messages=[], actions=[{
                "model_step_id": "step-1", "stable_tool_call_id": "call-1",
                "tool_name": "legacy_tool", "arguments": {},
            }], toolset=_toolset(), model_step=1,
        )
