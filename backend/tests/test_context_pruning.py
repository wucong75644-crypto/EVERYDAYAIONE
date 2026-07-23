"""统一确定性 ToolResult Pruning 合同测试。"""

from __future__ import annotations

import copy

from services.agent.runtime.context import prune_context


def _tool_turn(index: int, *, calls: int = 1) -> list[dict]:
    tool_calls = [
        {
            "id": f"call-{index}-{offset}",
            "type": "function",
            "function": {"name": "erp_agent", "arguments": "{}"},
        }
        for offset in range(calls)
    ]
    return [
        {"role": "user", "content": f"问题 {index}"},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        *[
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": f"结果 {index}-{offset} " + "x" * 2_500,
            }
            for offset, call in enumerate(tool_calls)
        ],
        {"role": "assistant", "content": f"回答 {index}"},
    ]


def test_pruning_below_half_capacity_does_not_mutate_messages() -> None:
    messages = _tool_turn(0)
    original = copy.deepcopy(messages)

    receipt = prune_context(
        messages,
        usable_input=100_000,
        model_step=1,
    )

    assert messages == original
    assert receipt.outcome == "below_threshold"
    assert receipt.pruned_tool_results == 0


def test_pruning_only_replaces_tools_before_last_three_user_turns() -> None:
    messages = [
        *(_tool_turn(0)),
        *(_tool_turn(1)),
        *(_tool_turn(2)),
        *(_tool_turn(3)),
    ]

    receipt = prune_context(messages, usable_input=1, model_step=4)

    tool_messages = [item for item in messages if item["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("[已归档]")
    assert all(
        not item["content"].startswith("[已归档]")
        for item in tool_messages[1:]
    )
    assert receipt.outcome == "pruned"
    assert receipt.eligible_tool_pairs == 1
    assert receipt.pruned_tool_results == 1
    assert receipt.tokens_after < receipt.tokens_before


def test_pruning_handles_complete_parallel_tool_group_atomically() -> None:
    messages = [
        *(_tool_turn(0, calls=2)),
        *(_tool_turn(1)),
        *(_tool_turn(2)),
        *(_tool_turn(3)),
    ]

    receipt = prune_context(messages, usable_input=1, model_step=4)

    first_group = [
        item
        for item in messages
        if item.get("tool_call_id") in {"call-0-0", "call-0-1"}
    ]
    assert len(first_group) == 2
    assert all(item["content"].startswith("[已归档]") for item in first_group)
    assert receipt.eligible_tool_pairs == 1
    assert receipt.pruned_tool_results == 2


def test_pruning_does_not_touch_incomplete_or_orphan_tool_groups() -> None:
    messages = [
        {"role": "user", "content": "旧问题"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-a", "function": {"name": "a"}},
                {"id": "call-b", "function": {"name": "b"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "A" * 2_500},
        {"role": "tool", "tool_call_id": "orphan", "content": "O" * 2_500},
        {"role": "user", "content": "问题 1"},
        {"role": "user", "content": "问题 2"},
        {"role": "user", "content": "问题 3"},
    ]
    original = copy.deepcopy(messages)

    receipt = prune_context(messages, usable_input=1, model_step=4)

    assert messages == original
    assert receipt.outcome == "no_eligible_results"
    assert receipt.pruned_tool_results == 0


def test_pruning_is_idempotent_for_already_archived_results() -> None:
    messages = [
        *(_tool_turn(0)),
        *(_tool_turn(1)),
        *(_tool_turn(2)),
        *(_tool_turn(3)),
    ]
    first = prune_context(messages, usable_input=1, model_step=4)
    after_first = copy.deepcopy(messages)

    second = prune_context(messages, usable_input=1, model_step=4)

    assert first.pruned_tool_results == 1
    assert messages == after_first
    assert second.pruned_tool_results == 0
    assert second.outcome == "no_eligible_results"


def test_tool_image_projection_does_not_count_as_a_user_turn() -> None:
    messages = [
        *(_tool_turn(0)),
        *(_tool_turn(1)),
        *(_tool_turn(2)),
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[系统：以下是工具返回的图片]"},
                {"type": "image_url", "image_url": {"url": "https://x.test/a"}},
            ],
        },
        *(_tool_turn(3)),
    ]

    receipt = prune_context(messages, usable_input=1, model_step=4)

    pruned_ids = {
        item.get("tool_call_id")
        for item in messages
        if item.get("role") == "tool"
        and item["content"].startswith("[已归档]")
    }
    assert pruned_ids == {"call-0-0"}
    assert receipt.pruned_tool_results == 1
