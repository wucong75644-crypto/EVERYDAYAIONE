"""context_compressor 单元测试 — token预算 + system去重 + 工具结果归档 + 循环摘要

注意：层1 工具结果截断已迁移到 tool_result_envelope.wrap()，
对应测试在 test_tool_result_envelope.py 中。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.context_compressor import (
    enforce_tool_budget,
    _identify_tool_turns,
    compact_stale_tool_results,
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


def _make_tool_loop_messages(num_turns: int, content_size: int = 2500) -> list:
    """构造 N 轮工具循环的 messages 列表

    Args:
        num_turns: 轮数
        content_size: 每条 tool result 的字符数（默认 2500，超过 2000 阈值会被归档）
    """
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
        # tool result（默认 >2000 字符，触发归档）
        messages.append({
            "role": "tool", "tool_call_id": f"tc{t}_0",
            "content": f"工具{t}的查询结果，包含大量数据 " + "x" * content_size,
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

    def test_short_results_not_compressed(self):
        """≤2000 字符的短结果不被压缩（即使超过 keep_turns）"""
        msgs = _make_tool_loop_messages(5, content_size=500)  # 每条 ~520 字符
        compacted = compact_stale_tool_results(msgs, keep_turns=2)
        assert compacted == 0
        for m in msgs:
            if m.get("role") == "tool":
                assert not m["content"].startswith("[已归档")

    def test_compacts_old_turns(self):
        """大结果（>2000 字符）超过 keep_turns 时被归档"""
        msgs = _make_tool_loop_messages(5, content_size=2500)
        compacted = compact_stale_tool_results(msgs, keep_turns=2)
        assert compacted == 3  # turn 0,1,2 的 tool 被压缩

        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        # 前 3 条被归档（保留元数据）
        for m in tool_msgs[:3]:
            assert m["content"].startswith("[已归档")
        # 后 2 条保持原文
        for m in tool_msgs[3:]:
            assert not m["content"].startswith("[已归档")

    def test_idempotent(self):
        """多次调用不会重复压缩"""
        msgs = _make_tool_loop_messages(4, content_size=2500)
        c1 = compact_stale_tool_results(msgs, keep_turns=2)
        c2 = compact_stale_tool_results(msgs, keep_turns=2)
        assert c1 == 2
        assert c2 == 0  # 第二次无新压缩

    def test_keep_turns_1(self):
        msgs = _make_tool_loop_messages(3, content_size=2500)
        compacted = compact_stale_tool_results(msgs, keep_turns=1)
        assert compacted == 2


class TestBuildTcNameMap:
    """_build_tc_name_map 公共函数"""

    def test_extracts_tool_names(self):
        from services.handlers.context_compressor import _build_tc_name_map
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "erp_agent", "arguments": "{}"}},
                {"id": "tc2", "type": "function", "function": {"name": "code_execute", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "result1"},
            {"role": "tool", "tool_call_id": "tc2", "content": "result2"},
        ]
        tc_map = _build_tc_name_map(msgs)
        assert tc_map["tc1"] == "erp_agent"
        assert tc_map["tc2"] == "code_execute"

    def test_empty_messages(self):
        from services.handlers.context_compressor import _build_tc_name_map
        assert _build_tc_name_map([]) == {}

    def test_no_tool_calls(self):
        from services.handlers.context_compressor import _build_tc_name_map
        msgs = [{"role": "assistant", "content": "just text"}]
        assert _build_tc_name_map(msgs) == {}


class TestEnforceToolBudgetSmartArchive:
    """enforce_tool_budget 使用 _extract_archive_meta 归档"""

    def test_archived_content_has_metadata(self):
        """归档后的内容应包含 [已归档] + 工具名，而不是旧的一刀切格式"""
        # 构造 3 轮工具调用，超预算迫使归档
        staged_content = (
            '<persisted-output>\n'
            'Output too large (50000 chars). '
            'Full output saved to: STAGING_DIR + "/tool_result_erp_agent_abc123.txt"\n\n'
            'Preview (first 2000 chars):\n'
            '商品编码 | 可售库存 | 仓库\n'
            'A001    | 450     | 上海\n'
            + 'B002    | 320     | 北京\n' * 100  # 填充到 >2000 字符
            + '...共 500 条记录\n'
            '</persisted-output>'
        )
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "erp_agent", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": staged_content},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc2", "type": "function", "function": {"name": "erp_agent", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc2", "content": staged_content},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc3", "type": "function", "function": {"name": "erp_agent", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc3", "content": "最新短结果"},
        ]

        # 设置极低预算，强制归档前两轮
        enforce_tool_budget(msgs, max_tokens=100)

        # 第一轮应被归档，且包含元数据
        archived = msgs[1]["content"]
        assert archived.startswith("[已归档]")
        assert "erp_agent" in archived
        assert "tool_result_erp_agent_abc123.txt" in archived
        # 不应是旧格式
        assert "工具结果已压缩" not in archived
