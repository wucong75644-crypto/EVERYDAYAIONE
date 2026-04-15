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
    _is_archived,
    _msg_tokens,
    enforce_tool_budget,
    enforce_history_budget_sync,
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


class TestFindReverseAccumulationCut:
    """反向累积 token 切点计算"""

    def test_empty_messages(self):
        from services.handlers.context_compressor import _find_reverse_accumulation_cut
        assert _find_reverse_accumulation_cut([], 1000) == 0

    def test_all_fit_in_budget(self):
        from services.handlers.context_compressor import _find_reverse_accumulation_cut
        msgs = [{"role": "user", "content": "short"}]
        cut = _find_reverse_accumulation_cut(msgs, max_tokens=10000)
        assert cut == 0  # 全部保留

    def test_cuts_oldest_when_over_budget(self):
        from services.handlers.context_compressor import _find_reverse_accumulation_cut
        msgs = [
            {"role": "user", "content": "x" * 5000},      # ~2000 tokens
            {"role": "assistant", "content": "y" * 5000},  # ~2000 tokens
            {"role": "user", "content": "z" * 500},        # ~200 tokens
        ]
        cut = _find_reverse_accumulation_cut(msgs, max_tokens=1000)
        # 预算只够最后一条，切点应在前面
        assert cut >= 1

    def test_respects_tool_pair_integrity(self):
        """切点落在 tool 消息上时，向前回退到 assistant(tool_calls)"""
        from services.handlers.context_compressor import _find_reverse_accumulation_cut
        msgs = [
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "t"}}]},
            {"role": "tool", "content": "result " + "x" * 3000},
            {"role": "user", "content": "current"},
        ]
        cut = _find_reverse_accumulation_cut(msgs, max_tokens=500)
        # 切点不应落在 tool 上（index 2），应回退到 assistant（index 1）或更前
        if cut > 0:
            assert msgs[cut].get("role") != "tool"


class TestAdjustCutForToolPairs:
    """tool_calls + tool 配对不拆分"""

    def test_cut_on_tool_backs_up_to_assistant(self):
        from services.handlers.context_compressor import _adjust_cut_for_tool_pairs
        msgs = [
            {"role": "user", "content": "query"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "t"}}]},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "next"},
        ]
        # 切点在 tool(index=2) → 应回退到 assistant(index=1)
        assert _adjust_cut_for_tool_pairs(msgs, 2) == 1

    def test_cut_on_non_tool_unchanged(self):
        from services.handlers.context_compressor import _adjust_cut_for_tool_pairs
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        assert _adjust_cut_for_tool_pairs(msgs, 2) == 2

    def test_cut_beyond_length_unchanged(self):
        from services.handlers.context_compressor import _adjust_cut_for_tool_pairs
        msgs = [{"role": "user", "content": "hi"}]
        assert _adjust_cut_for_tool_pairs(msgs, 5) == 5

    def test_orphan_tool_no_matching_assistant(self):
        """tool 消息前面没有 assistant(tool_calls)，保持原切点"""
        from services.handlers.context_compressor import _adjust_cut_for_tool_pairs
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "tool", "content": "orphan result"},
            {"role": "user", "content": "next"},
        ]
        assert _adjust_cut_for_tool_pairs(msgs, 1) == 1


class TestEnforceBudget:
    """token 预算兜底（反向累积策略）"""

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
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "current"},
            {"role": "assistant", "content": "response"},
        ]
        enforce_budget(messages, max_tokens=5000)
        archived = [m for m in messages if m["content"] == "[已归档]"]
        assert len(archived) > 0

    def test_system_messages_never_archived(self):
        """system 消息始终保留，不被归档"""
        messages = [
            {"role": "system", "content": "你是AI助手"},
            {"role": "user", "content": "x" * 50000},
            {"role": "user", "content": "current"},
        ]
        enforce_budget(messages, max_tokens=100)
        assert messages[0]["content"] == "你是AI助手"

    def test_protects_tail_messages(self):
        messages = [
            {"role": "user", "content": "x" * 10000},
            {"role": "user", "content": "current question"},
        ]
        enforce_budget(messages, max_tokens=100)
        assert messages[-1]["content"] == "current question"

    def test_tool_pair_not_split(self):
        """enforce_budget 不会拆分 assistant(tool_calls) + tool 配对"""
        messages = [
            {"role": "user", "content": "x" * 20000},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "t"}}]},
            {"role": "tool", "content": "result"},
            {"role": "user", "content": "current " + "y" * 5000},
        ]
        enforce_budget(messages, max_tokens=3000)
        # assistant 和 tool 应该同时被归档或同时保留
        ast_archived = messages[1]["content"] == "[已归档]"
        tool_archived = messages[2]["content"] == "[已归档]"
        assert ast_archived == tool_archived


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


# ============================================================
# _is_archived: 多模态兼容
# ============================================================


class TestIsArchived:
    def test_archived_string(self):
        assert _is_archived({"content": "[已归档] 工具结果已压缩"}) is True

    def test_normal_string(self):
        assert _is_archived({"content": "正常内容"}) is False

    def test_multimodal_list(self):
        """list content 不视为已归档"""
        assert _is_archived({"content": [{"type": "text", "text": "[已归档"}]}) is False

    def test_empty_content(self):
        assert _is_archived({"content": ""}) is False
        assert _is_archived({}) is False


# ============================================================
# enforce_tool_budget: 工具结果分桶
# ============================================================


def _make_tool_turn(turn_idx: int, result_size: int = 500):
    """构造一轮 assistant(tool_calls) + tool(result) 消息"""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": f"tool_{turn_idx}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": f"tc_{turn_idx}",
            "content": "x" * result_size,
        },
    ]


class TestEnforceToolBudget:
    def test_within_budget_no_change(self):
        """预算内不压缩"""
        msgs = [*_make_tool_turn(1, 100), *_make_tool_turn(2, 100)]
        original_contents = [m.get("content") for m in msgs]
        enforce_tool_budget(msgs, max_tokens=10000)
        assert [m.get("content") for m in msgs] == original_contents

    def test_over_budget_compacts_oldest(self):
        """超预算压缩最旧的工具结果"""
        # 3 轮，每轮 tool result 5000 字符 ≈ 2000 token
        msgs = [*_make_tool_turn(1, 5000), *_make_tool_turn(2, 5000), *_make_tool_turn(3, 5000)]
        enforce_tool_budget(msgs, max_tokens=3000)  # 只够 ~1.5 轮
        # 第 1 轮的 tool 应被归档
        assert msgs[1]["content"].startswith("[已归档")
        # 最近 2 轮（第 2、3 轮）的 tool 应保留
        assert not msgs[3]["content"].startswith("[已归档")
        assert not msgs[5]["content"].startswith("[已归档")

    def test_protects_last_2_turns(self):
        """保护最近 2 轮不压缩"""
        msgs = [*_make_tool_turn(1, 5000), *_make_tool_turn(2, 5000), *_make_tool_turn(3, 5000)]
        enforce_tool_budget(msgs, max_tokens=1)  # 极小预算
        # 最近 2 轮（第 2、3 轮）的 tool 仍保留
        assert not msgs[3]["content"].startswith("[已归档")
        assert not msgs[5]["content"].startswith("[已归档")

    def test_no_tool_messages_noop(self):
        """没有 tool 消息不报错"""
        msgs = [{"role": "user", "content": "hello"}]
        enforce_tool_budget(msgs, max_tokens=100)
        assert msgs[0]["content"] == "hello"

    def test_already_archived_skip(self):
        """已归档的不重复压缩"""
        msgs = [*_make_tool_turn(1, 5000), *_make_tool_turn(2, 5000), *_make_tool_turn(3, 5000)]
        msgs[1]["content"] = "[已归档] 工具结果已压缩（原始 5000 字符）"
        enforce_tool_budget(msgs, max_tokens=1)
        # 已归档的保持不变
        assert "原始 5000" in msgs[1]["content"]


# ============================================================
# enforce_history_budget_sync: 历史桶（同步版）
# ============================================================


class TestEnforceHistoryBudgetSync:
    def test_within_budget_no_change(self):
        """预算内不压缩"""
        msgs = [
            {"role": "user", "content": "短消息"},
            {"role": "assistant", "content": "短回复"},
        ]
        enforce_history_budget_sync(msgs, max_tokens=10000)
        assert msgs[0]["content"] == "短消息"

    def test_over_budget_removes_low_score_first(self):
        """超预算时低分消息先被淘汰"""
        msgs = [
            {"role": "user", "content": "好的"},          # 低分（废话）
            {"role": "user", "content": "x" * 5000},      # 高分（长消息）
            {"role": "assistant", "content": "x" * 5000},  # 高分
            {"role": "user", "content": "订单号1234567890"}, # 高分（实体）
            {"role": "user", "content": "当前问题"},        # 受保护（最后 4 条之一）
        ]
        enforce_history_budget_sync(msgs, max_tokens=2000)
        # "好的"（低分）应先被淘汰
        assert msgs[0]["content"] == "[已归档]"

    def test_protects_last_4(self):
        """保护最后 4 条消息"""
        msgs = [
            {"role": "user", "content": "x" * 10000},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "user", "content": "d"},
        ]
        enforce_history_budget_sync(msgs, max_tokens=100)
        # 只有第一条可被淘汰
        assert msgs[0]["content"] == "[已归档]"
        # 后 4 条受保护
        assert msgs[1]["content"] == "a"
        assert msgs[4]["content"] == "d"

    def test_no_history_noop(self):
        """没有 user/assistant 消息不报错"""
        msgs = [{"role": "system", "content": "system prompt"}]
        enforce_history_budget_sync(msgs, max_tokens=100)
        assert msgs[0]["content"] == "system prompt"

    def test_skips_archived_messages(self):
        """已归档的消息不参与打分/淘汰"""
        msgs = [
            {"role": "user", "content": "[已归档]"},
            {"role": "user", "content": "x" * 10000},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "user", "content": "d"},
        ]
        enforce_history_budget_sync(msgs, max_tokens=100)
        # 已归档的保持不变
        assert msgs[0]["content"] == "[已归档]"
