"""Runtime v3 permission-mode prompt compatibility tests."""

from services.agent.runtime.context.mode_prompt import (
    normalize_permission_mode,
    render_runtime_mode_prompt,
)
from services.agent.runtime.production_model import _runtime_messages


def test_normalize_permission_mode_keeps_legacy_inputs() -> None:
    assert normalize_permission_mode(True) == "plan"
    assert normalize_permission_mode("true") == "plan"
    assert normalize_permission_mode(False) == "auto"
    assert normalize_permission_mode(None) == "auto"
    assert normalize_permission_mode("ASK") == "ask"
    assert normalize_permission_mode("invalid") == "auto"


def test_runtime_mode_prompt_contains_shared_rules_and_current_mode() -> None:
    prompt = render_runtime_mode_prompt("plan")

    assert "<permission_mode>" in prompt
    assert "## plan 模式" in prompt
    assert "<current_mode>plan</current_mode>" in prompt
    assert "**MUST NOT** 调用 `erp_agent`" in prompt


def test_runtime_model_messages_use_run_permission_mode() -> None:
    from types import SimpleNamespace

    messages, mode = _runtime_messages(
        context={"messages": [{"role": "user", "content": "先规划"}]},
        definition=SimpleNamespace(system_prompt="Runtime base"),
        payload={"params": {"permission_mode": "plan"}},
        model_id="qwen3.5-plus",
        input_message_id=None,
    )

    assert mode == "plan"
    assert messages[0]["role"] == "system"
    assert "Runtime base" in messages[0]["content"]
    assert "<current_mode>plan</current_mode>" in messages[0]["content"]
