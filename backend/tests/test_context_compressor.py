"""context_compressor 单元测试 — token预算 + system去重 + 工具结果归档 + 循环摘要

注意：层1 工具结果截断已迁移到 tool_result_envelope.wrap()，
对应测试在 test_tool_result_envelope.py 中。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, patch
from services.handlers.context_compressor import (
    _build_loop_summary_input,
    _identify_tool_turns,
    compact_stale_tool_results,
    compact_loop_with_summary,
    deduplicate_system_prompts,
    enforce_budget,
    estimate_tokens,
)


# ============================================================
# 层4: Token 估算
# ============================================================


class TestEstimateTokens:
    """token 估算"""

    def test_simple_text_messages(self):
        msgs = [{"content": "x" * 250}]  # 250 chars / 2.5 = 100 tokens
        assert estimate_tokens(msgs) == 100

    def test_mixed_content(self):
        msgs = [
            {"content": "hello"},
            {"content": [{"text": "world", "type": "text"}]},
        ]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_tool_calls_counted(self):
        msgs = [{"content": "", "tool_calls": [
            {"function": {"arguments": '{"query": "test" }' * 10}}
        ]}]
        tokens = estimate_tokens(msgs)
        assert tokens > 0

    def test_empty_messages(self):
        assert estimate_tokens([]) == 0


# ============================================================
# 层4: System Prompt 去重
# ============================================================


class TestDeduplicateSystemPrompts:
    """工具循环 system prompt 去重"""

    def test_keeps_latest_only(self):
        messages = [
            {"role": "system", "content": "已识别编码: A=001"},
            {"role": "user", "content": "查库存"},
            {"role": "system", "content": "已识别编码: A=001, B=002 | 已用工具: stock"},
            {"role": "user", "content": "继续"},
        ]
        deduplicate_system_prompts(messages)
        ctx_msgs = [m for m in messages if "已识别编码" in m.get("content", "")]
        assert len(ctx_msgs) == 1
        assert "B=002" in ctx_msgs[0]["content"]

    def test_no_ctx_prompts_unchanged(self):
        messages = [
            {"role": "system", "content": "你是AI助手"},
            {"role": "user", "content": "你好"},
        ]
        original_len = len(messages)
        deduplicate_system_prompts(messages)
        assert len(messages) == original_len

    def test_single_ctx_prompt_kept(self):
        messages = [
            {"role": "system", "content": "已用工具: erp_agent"},
        ]
        deduplicate_system_prompts(messages)
        assert len(messages) == 1


# ============================================================
# 层4: Token 预算兜底
# ============================================================


class TestEnforceBudget:
    """token 预算兜底"""

    def test_under_budget_unchanged(self):
        messages = [{"role": "user", "content": "hi"}]
        enforce_budget(messages, max_tokens=1000)
        assert messages[0]["content"] == "hi"

    def test_over_budget_archives_old(self):
        messages = [
            {"role": "user", "content": "x" * 25000},
            {"role": "assistant", "content": "y" * 25000},
            {"role": "user", "content": "z" * 25000},
            {"role": "assistant", "content": "w" * 25000},
            # 后面6条是 protected_tail
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "response"},
        ]
        enforce_budget(messages, max_tokens=5000)
        archived = [m for m in messages if m["content"] == "[已归档]"]
        assert len(archived) > 0

    def test_protects_tail_messages(self):
        messages = [
            {"role": "user", "content": "x" * 10000},
            {"role": "user", "content": "current question"},
        ]
        enforce_budget(messages, max_tokens=100)
        # 最后几条不应被归档（protected_tail）
        assert messages[-1]["content"] == "current question"


# ============================================================
# 层4: 轮次识别
# ============================================================


def _make_tool_loop_messages(num_turns: int) -> list:
    """构造 N 轮工具循环的 messages 列表"""
    messages = [
        {"role": "system", "content": "你是AI助手"},
        {"role": "user", "content": "帮我查一下"},
    ]
    for t in range(num_turns):
        # assistant + tool_calls
        messages.append({
            "role": "assistant", "content": f"turn{t}思考",
            "tool_calls": [
                {"id": f"tc{t}_0", "type": "function",
                 "function": {"name": f"tool_{t}", "arguments": "{}"}},
            ],
        })
        # tool result
        messages.append({
            "role": "tool", "tool_call_id": f"tc{t}_0",
            "content": f"工具{t}的查询结果，包含大量数据 " + "x" * 500,
        })
    return messages


class TestIdentifyToolTurns:
    """轮次边界识别"""

    def test_identifies_correct_turns(self):
        msgs = _make_tool_loop_messages(3)
        turns = _identify_tool_turns(msgs)
        assert len(turns) == 3
        # 每轮 1 个 tool
        for turn_tools in turns:
            assert len(turn_tools) == 1

    def test_empty_messages(self):
        assert _identify_tool_turns([]) == []

    def test_no_tool_calls(self):
        msgs = [
            {"role": "system", "content": "hi"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert _identify_tool_turns(msgs) == []

    def test_multi_tool_per_turn(self):
        msgs = [
            {"role": "user", "content": "查库存和订单"},
            {"role": "assistant", "content": "",
             "tool_calls": [
                 {"id": "a", "function": {"name": "stock"}},
                 {"id": "b", "function": {"name": "order"}},
             ]},
            {"role": "tool", "tool_call_id": "a", "content": "库存100"},
            {"role": "tool", "tool_call_id": "b", "content": "订单50"},
        ]
        turns = _identify_tool_turns(msgs)
        assert len(turns) == 1
        assert len(turns[0]) == 2  # 2 个 tool 属于同一轮


# ============================================================
# 层4: 旧工具结果归档
# ============================================================


class TestCompactStaleToolResults:
    """旧工具结果归档"""

    def test_no_compact_when_few_turns(self):
        msgs = _make_tool_loop_messages(2)
        compacted = compact_stale_tool_results(msgs, keep_turns=2)
        assert compacted == 0
        # 所有 tool 消息保持原文
        for m in msgs:
            if m.get("role") == "tool":
                assert not m["content"].startswith("[已归档")

    def test_compacts_old_turns(self):
        msgs = _make_tool_loop_messages(5)
        compacted = compact_stale_tool_results(msgs, keep_turns=2)
        assert compacted == 3  # turn 0,1,2 的 tool 被压缩

        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        # 前 3 条被归档
        for m in tool_msgs[:3]:
            assert m["content"].startswith("[已归档")
        # 后 2 条保持原文
        for m in tool_msgs[3:]:
            assert not m["content"].startswith("[已归档")

    def test_idempotent(self):
        """多次调用不会重复压缩"""
        msgs = _make_tool_loop_messages(4)
        c1 = compact_stale_tool_results(msgs, keep_turns=2)
        c2 = compact_stale_tool_results(msgs, keep_turns=2)
        assert c1 == 2
        assert c2 == 0  # 第二次无新压缩

    def test_keep_turns_1(self):
        msgs = _make_tool_loop_messages(3)
        compacted = compact_stale_tool_results(msgs, keep_turns=1)
        assert compacted == 2


# ============================================================
# 层5: 循环内 LLM 摘要
# ============================================================


class TestCompactLoopWithSummary:
    """循环内摘要"""

    @pytest.mark.asyncio
    async def test_no_trigger_under_threshold(self):
        msgs = _make_tool_loop_messages(2)
        result = await compact_loop_with_summary(msgs, max_tokens=50000)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_trigger_few_turns(self):
        """只有 2 轮时即使超阈值也不触发（没有可压缩的）"""
        msgs = _make_tool_loop_messages(2)
        # 人为制造超阈值
        result = await compact_loop_with_summary(msgs, max_tokens=10, trigger_ratio=0.01)
        assert result is False

    @pytest.mark.asyncio
    async def test_triggers_and_compresses(self):
        """超阈值 + 足够轮次 → 触发摘要"""
        msgs = _make_tool_loop_messages(5)
        original_len = len(msgs)

        with patch(
            "services.context_summarizer._call_summary_model",
            new=AsyncMock(return_value="摘要：查了5个工具，库存100，订单50"),
        ):
            result = await compact_loop_with_summary(
                msgs, max_tokens=10, trigger_ratio=0.01,
            )

        assert result is True
        assert len(msgs) < original_len
        # 应该有一条摘要 system 消息
        summary_msgs = [m for m in msgs if "[工具循环摘要]" in m.get("content", "")]
        assert len(summary_msgs) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_model_failure(self):
        """LLM 调用失败 → 跳过，不崩溃"""
        msgs = _make_tool_loop_messages(5)
        original_len = len(msgs)

        with patch(
            "services.context_summarizer._call_summary_model",
            new=AsyncMock(return_value=None),
        ):
            result = await compact_loop_with_summary(
                msgs, max_tokens=10, trigger_ratio=0.01,
            )

        assert result is False
        assert len(msgs) == original_len  # 未改动


# ============================================================
# 层5: 摘要输入格式化
# ============================================================


class TestBuildLoopSummaryInput:
    """_build_loop_summary_input 格式化测试"""

    def test_formats_assistant_with_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": "让我查一下",
             "tool_calls": [{"function": {"name": "local_stock_query"}}]},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert "AI 调用工具: local_stock_query" in result
        assert "AI: 让我查一下" in result

    def test_formats_tool_result(self):
        msgs = [
            {"role": "tool", "tool_call_id": "tc1",
             "content": "库存100件，金额¥5,000"},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert "工具结果: 库存100件" in result

    def test_formats_system_context(self):
        msgs = [
            {"role": "system", "content": "已识别编码: A→001"},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert "系统: 已识别编码" in result

    def test_truncates_long_content(self):
        msgs = [
            {"role": "tool", "tool_call_id": "tc1",
             "content": "x" * 500},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert "..." in result
        assert len(result) < 500

    def test_skips_long_system(self):
        """超过200字的 system 消息不纳入摘要"""
        msgs = [
            {"role": "system", "content": "x" * 300},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert result == ""

    def test_empty_indices(self):
        msgs = _make_tool_loop_messages(3)
        result = _build_loop_summary_input(msgs, [])
        assert result == ""

    def test_assistant_no_content(self):
        """assistant 无文字内容但有 tool_calls"""
        msgs = [
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"name": "erp_agent"}}]},
        ]
        result = _build_loop_summary_input(msgs, [0])
        assert "AI 调用工具: erp_agent" in result
        assert "AI:" not in result  # content=None 不输出
