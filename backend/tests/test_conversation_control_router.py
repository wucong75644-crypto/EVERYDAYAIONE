import json

from config.conversation_control_prompt import build_conversation_control_prompt
from config.conversation_control_tools import build_conversation_control_tools
from services.conversation_control_router import (
    ConversationControlRouter,
    ConversationControlState,
    ControlAction,
)


def _payload(action: str, reason: str = "") -> dict:
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "conversation_control",
                        "arguments": json.dumps({"action": action, "reason": reason}),
                    },
                }],
            },
        }],
    }


def test_control_prompt_injects_current_state_without_task_id():
    prompt = build_conversation_control_prompt(
        state="paused",
        has_running_task=False,
        has_paused_task=True,
        latest_task_summary="当前对话中的最近任务",
    )
    assert "state: paused" in prompt
    assert "has_paused_task: true" in prompt
    assert "task_id:" not in prompt


def test_control_tool_has_explicit_action_contract():
    tool = build_conversation_control_tools()[0]["function"]
    assert tool["name"] == "conversation_control"
    assert tool["parameters"]["properties"]["action"]["enum"] == [
        "pause", "resume", "cancel", "none",
    ]


def test_parse_control_actions_and_state_guards():
    router = ConversationControlRouter()
    running = ConversationControlState("running", True, False)
    paused = ConversationControlState("paused", False, True)

    assert router._parse(_payload("pause"), running).action is ControlAction.PAUSE
    assert router._parse(_payload("cancel"), running).action is ControlAction.CANCEL
    assert router._parse(_payload("resume"), paused).action is ControlAction.RESUME
    assert router._parse(_payload("resume"), running).action is ControlAction.NONE
    assert router._parse(_payload("pause"), paused).action is ControlAction.NONE


def test_parse_unknown_or_missing_tool_is_safe_noop():
    router = ConversationControlRouter()
    state = ConversationControlState("running", True, False)
    assert router._parse({"choices": [{"message": {}}]}, state).action is ControlAction.NONE
    unknown = _payload("pause")
    unknown["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "text_chat"
    assert router._parse(unknown, state).action is ControlAction.NONE
