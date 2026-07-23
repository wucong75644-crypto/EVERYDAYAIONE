"""旧 Context Compressor 预算与归档识别测试。"""

from services.handlers.context_compressor import (
    _is_archived,
    enforce_history_budget_sync,
    enforce_tool_budget,
)


def _tool_turn(turn: int, size: int = 500) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": f"tool_{turn}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": f"tc_{turn}",
            "content": "x" * size,
        },
    ]


def test_is_archived_recognizes_archived_text() -> None:
    assert _is_archived({"content": "[已归档] 结果"}) is True


def test_is_archived_rejects_normal_text() -> None:
    assert _is_archived({"content": "正常内容"}) is False


def test_is_archived_recognizes_multimodal_text() -> None:
    assert _is_archived({
        "content": [{"type": "text", "text": "[已归档]"}],
    }) is True


def test_is_archived_rejects_normal_multimodal_text() -> None:
    assert _is_archived({
        "content": [{"type": "text", "text": "正常文本"}],
    }) is False


def test_is_archived_handles_empty_content() -> None:
    assert _is_archived({}) is False


def test_tool_budget_keeps_messages_within_budget() -> None:
    messages = [*_tool_turn(1, 100), *_tool_turn(2, 100)]
    original = [message.get("content") for message in messages]
    enforce_tool_budget(messages, max_tokens=10_000)
    assert [message.get("content") for message in messages] == original


def test_tool_budget_archives_oldest_result() -> None:
    messages = [
        *_tool_turn(1, 5_000),
        *_tool_turn(2, 5_000),
        *_tool_turn(3, 5_000),
    ]
    enforce_tool_budget(messages, max_tokens=3_000)
    assert messages[1]["content"].startswith("[已归档")


def test_tool_budget_protects_last_two_turns() -> None:
    messages = [
        *_tool_turn(1, 5_000),
        *_tool_turn(2, 5_000),
        *_tool_turn(3, 5_000),
    ]
    enforce_tool_budget(messages, max_tokens=1)
    assert not messages[3]["content"].startswith("[已归档")
    assert not messages[5]["content"].startswith("[已归档")


def test_tool_budget_handles_no_tools() -> None:
    no_tools = [{"role": "user", "content": "hello"}]
    enforce_tool_budget(no_tools, max_tokens=100)
    assert no_tools[0]["content"] == "hello"


def test_tool_budget_skips_existing_archive() -> None:
    messages = [
        *_tool_turn(1, 5_000),
        *_tool_turn(2, 5_000),
        *_tool_turn(3, 5_000),
    ]
    messages[1]["content"] = "[已归档] 原始 5000 字符"
    enforce_tool_budget(messages, max_tokens=1)
    assert "原始 5000" in messages[1]["content"]


def test_history_budget_keeps_messages_within_budget() -> None:
    messages = [
        {"role": "user", "content": "短消息"},
        {"role": "assistant", "content": "短回复"},
    ]
    enforce_history_budget_sync(messages, max_tokens=10_000)
    assert messages[0]["content"] == "短消息"


def test_history_budget_removes_low_score_first() -> None:
    messages = [
        {"role": "user", "content": "好的"},
        {"role": "user", "content": "x" * 5_000},
        {"role": "assistant", "content": "x" * 5_000},
        {"role": "user", "content": "订单号1234567890"},
        {"role": "user", "content": "当前问题"},
    ]
    enforce_history_budget_sync(messages, max_tokens=2_000)
    assert messages[0]["content"] == "[已归档]"


def test_history_budget_protects_last_four_messages() -> None:
    messages = [
        {"role": "user", "content": "x" * 10_000},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ]
    enforce_history_budget_sync(messages, max_tokens=100)
    assert messages[0]["content"] == "[已归档]"
    assert messages[1]["content"] != "[已归档]"
    assert messages[4]["content"] == "d"


def test_history_budget_handles_no_history() -> None:
    no_history = [{"role": "system", "content": "system prompt"}]
    enforce_history_budget_sync(no_history, max_tokens=100)
    assert no_history[0]["content"] == "system prompt"


def test_history_budget_skips_existing_archive() -> None:
    messages = [
        {"role": "user", "content": "[已归档]"},
        {"role": "user", "content": "x" * 10_000},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "d"},
    ]
    enforce_history_budget_sync(messages, max_tokens=100)
    assert messages[0]["content"] == "[已归档]"
