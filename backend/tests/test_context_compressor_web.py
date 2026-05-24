"""Web 端上下文压缩单元测试 — _identify_user_turns + compact_stale_by_user_turns

测试目标：
1. 按用户对话切分轮次（不是工具调用）
2. 容量未到阈值 → 不压缩
3. 用户对话数 ≤ keep_user_turns → 不压缩
4. 容量+轮次都满足 → 旧轮次工具结果归档
5. 短结果跳过、已归档跳过
6. 企微 compact_stale_tool_results 不受影响
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.context_compressor import (
    _identify_user_turns,
    compact_stale_by_user_turns,
    compact_stale_tool_results,
)


# ============================================================
# 辅助：消息构造
# ============================================================


def _user(text="q"):
    return {"role": "user", "content": text}


def _assistant_text(text="reply"):
    return {"role": "assistant", "content": text}


def _assistant_tc(tc_id, name="code_execute"):
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": tc_id, "function": {"name": name, "arguments": "{}"}}
        ],
    }


def _tool(tc_id, content="result"):
    return {"role": "tool", "tool_call_id": tc_id, "content": content}


def _big_tool(tc_id, size=3000):
    """超过 2000 字符的工具结果，会触发压缩。"""
    return {"role": "tool", "tool_call_id": tc_id, "content": "x" * size}


# ============================================================
# _identify_user_turns
# ============================================================


class TestIdentifyUserTurns:
    """按用户消息切分对话回合"""

    def test_empty_messages(self):
        assert _identify_user_turns([]) == []

    def test_no_user_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            _assistant_text("hi"),
        ]
        assert _identify_user_turns(msgs) == []

    def test_single_user_message(self):
        msgs = [_user("q1")]
        assert _identify_user_turns(msgs) == [(0, 1)]

    def test_user_then_assistant(self):
        msgs = [_user("q1"), _assistant_text("a1")]
        assert _identify_user_turns(msgs) == [(0, 2)]

    def test_multiple_turns_with_tools(self):
        """典型场景：每轮包含 user + assistant tool_calls + tool + assistant"""
        msgs = [
            {"role": "system", "content": "sys"},
            _user("q1"),
            _assistant_tc("tc1"),
            _tool("tc1"),
            _assistant_text("a1"),
            _user("q2"),
            _assistant_tc("tc2"),
            _tool("tc2"),
            _assistant_text("a2"),
        ]
        # turn 1: idx 1-4 (user q1 → a1)
        # turn 2: idx 5-8 (user q2 → a2)
        assert _identify_user_turns(msgs) == [(1, 5), (5, 9)]

    def test_one_user_multiple_tool_calls(self):
        """一次用户对话内多次连续工具调用，应该算 1 轮（不是多轮）"""
        msgs = [
            _user("q1"),
            _assistant_tc("tc1"),
            _tool("tc1"),
            _assistant_tc("tc2"),  # 第二次工具调用
            _tool("tc2"),
            _assistant_tc("tc3"),  # 第三次工具调用
            _tool("tc3"),
            _assistant_text("a1"),
        ]
        # 一个用户对话回合
        turns = _identify_user_turns(msgs)
        assert turns == [(0, 8)], f"应只切分为 1 轮，实际：{turns}"

    def test_consecutive_user_messages_steer(self):
        """steer 场景：用户连续发消息（中间没等 LLM 回复）"""
        msgs = [
            _user("q1"),
            _assistant_text("a1"),
            _user("q2"),
            _user("q3-steer"),  # 用户打断
            _assistant_text("a2"),
        ]
        assert _identify_user_turns(msgs) == [(0, 2), (2, 3), (3, 5)]


# ============================================================
# compact_stale_by_user_turns - 容量触发
# ============================================================


class TestWebCompactCapacityTrigger:
    """容量触发逻辑"""

    def test_capacity_not_reached_no_compress(self):
        """上下文未到阈值时，即使轮次很多也不压缩"""
        msgs = []
        for i in range(20):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))  # 大结果但总量不大

        # 200K 阈值很高，当前 messages 远低于
        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.7, max_tokens=200000,
        )
        assert result == 0
        # 验证没有任何消息被改写
        for i in range(20):
            assert not msgs[i * 3 + 2]["content"].startswith("[已归档")

    def test_capacity_reached_compresses(self):
        """容量到达阈值 + 轮次足够 → 压缩旧轮次"""
        msgs = []
        for i in range(15):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        # 用极低阈值强制触发
        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )
        assert result == 5, f"应压缩 5 个旧轮次的 tool 消息，实际 {result}"


# ============================================================
# compact_stale_by_user_turns - 轮次保留
# ============================================================


class TestWebCompactTurnPreservation:
    """用户对话保留逻辑"""

    def test_turns_below_keep_no_compress(self):
        """用户对话数 ≤ keep_user_turns 时不压缩"""
        msgs = []
        for i in range(5):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )
        assert result == 0

    def test_exact_keep_count_no_compress(self):
        """正好等于 keep_user_turns 也不压缩"""
        msgs = []
        for i in range(10):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )
        assert result == 0

    def test_recent_turns_preserved(self):
        """最近 N 个用户对话的工具结果不被压缩"""
        msgs = []
        for i in range(15):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )

        # 前 5 个对话回合的 tool 应被归档
        for i in range(5):
            tool_msg = msgs[i * 3 + 2]
            assert tool_msg["content"].startswith("[已归档"), \
                f"第 {i} 轮应被归档"
        # 后 10 个对话回合的 tool 不应被归档
        for i in range(5, 15):
            tool_msg = msgs[i * 3 + 2]
            assert not tool_msg["content"].startswith("[已归档"), \
                f"第 {i} 轮不应被归档"

    def test_one_user_multiple_tools_kept_together(self):
        """一次用户对话内所有 tool 结果同进同退"""
        # 11 个用户对话，第 1 个有 3 个工具调用
        msgs = []
        # 用户对话 0：3 个工具调用
        msgs.append(_user("q0"))
        msgs.append(_assistant_tc("tc0a"))
        msgs.append(_big_tool("tc0a", size=3000))
        msgs.append(_assistant_tc("tc0b"))
        msgs.append(_big_tool("tc0b", size=3000))
        msgs.append(_assistant_tc("tc0c"))
        msgs.append(_big_tool("tc0c", size=3000))
        # 用户对话 1-10：每个 1 个工具调用
        for i in range(1, 11):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )

        # 用户对话 0 的 3 个 tool 都应被归档
        for idx in [2, 4, 6]:
            assert msgs[idx]["content"].startswith("[已归档"), \
                f"对话 0 的 idx={idx} 应被归档"
        # 用户对话 1-10 的 tool 不应被归档
        for i in range(10):
            idx = 7 + i * 3 + 2  # base=7（对话0占0-6），每轮3条，tool在第3条
            assert not msgs[idx]["content"].startswith("[已归档"), \
                f"近 10 轮 idx={idx} 不应被归档"


# ============================================================
# compact_stale_by_user_turns - 压缩规则
# ============================================================


class TestWebCompactRules:
    """压缩内容规则"""

    def test_short_results_skipped(self):
        """短结果（≤2000 字符）不压缩"""
        msgs = []
        for i in range(15):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_tool(f"tc{i}", content="short result"))

        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )
        assert result == 0

    def test_already_archived_not_recompressed(self):
        """已归档（[已归档 前缀）的不二次压缩"""
        msgs = []
        for i in range(15):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_tool(f"tc{i}", content="[已归档] 旧的"))

        result = compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )
        assert result == 0

    def test_compressed_content_has_archived_prefix(self):
        """压缩后内容以 [已归档 开头"""
        msgs = []
        for i in range(15):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"tc{i}", name="code_execute"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.0, max_tokens=200000,
        )

        for i in range(5):  # 前 5 个被压缩
            tool_msg = msgs[i * 3 + 2]
            assert tool_msg["content"].startswith("[已归档")


# ============================================================
# compact_stale_tool_results 不受影响（企微链路保护）
# ============================================================


class TestWeComUnchanged:
    """验证企微旧函数行为不变"""

    def test_wecom_compress_still_works(self):
        """compact_stale_tool_results 按工具轮次压缩，行为不变"""
        msgs = []
        for i in range(15):
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        # 按工具轮次 = 15 轮，保留 10，压缩 5 轮共 5 个 tool 消息
        result = compact_stale_tool_results(msgs, keep_turns=10)
        assert result == 5


# ============================================================
# Web 4 步压缩链路整体不应在低容量时压 schema
# ============================================================


class TestWebFullPipelineRespectsCapacity:
    """验证 4 步压缩链路在低容量时不应触发任何归档（防止 Step 2/3 绕过 Step 1 阈值）"""

    def test_low_capacity_no_step_triggers(self):
        """上下文远低于阈值时，全部 4 步都不应归档任何 tool 消息。

        防回归：之前 Step 2 用 6K 桶，Web 上 2-3 轮就把 file_analyze schema 干掉了。
        本测试模拟 12 轮 file_analyze + code_execute（约 24K，占 200K 的 12%）。
        """
        from services.handlers.context_compressor import (
            enforce_tool_budget, enforce_history_budget_sync,
        )

        msgs = []
        for i in range(12):
            msgs.append(_user(f"q{i}"))
            msgs.append(_assistant_tc(f"fa{i}", name="file_analyze"))
            # 包含 schema 关键信息
            msgs.append({
                "role": "tool", "tool_call_id": f"fa{i}",
                "content": "schema: 销售金额(float), 店铺名称(str)" + "x" * 2500,
            })
            msgs.append(_assistant_tc(f"ce{i}", name="code_execute"))
            msgs.append(_big_tool(f"ce{i}", size=2500))
            msgs.append(_assistant_text(f"reply {i}"))

        # 模拟 chat_handler 的 Web 链路调用顺序
        compact_stale_by_user_turns(
            msgs, keep_user_turns=10, capacity_trigger=0.7, max_tokens=200000,
        )
        enforce_tool_budget(msgs, 100000)  # Web 大预算
        enforce_history_budget_sync(msgs, 50000)  # Web 大预算

        # 验证：无任何 tool 消息被归档
        archived = sum(
            1 for m in msgs
            if m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("[已归档")
        )
        assert archived == 0, f"低容量时不应归档，但有 {archived} 条被归档"

        # 验证：file_analyze 的 schema 仍可读取
        for m in msgs:
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                if "schema: 销售金额" in m["content"]:
                    return  # 至少有一个 schema 完整保留
        assert False, "应至少有一个 file_analyze schema 完整保留"

    def test_wecom_small_budget_still_aggressive(self):
        """企微链路用小预算时仍能正常归档（验证企微行为不变）"""
        from services.handlers.context_compressor import (
            compact_stale_tool_results, enforce_tool_budget,
        )

        msgs = []
        for i in range(12):
            msgs.append(_assistant_tc(f"tc{i}"))
            msgs.append(_big_tool(f"tc{i}", size=3000))

        compact_stale_tool_results(msgs, keep_turns=10)
        enforce_tool_budget(msgs, 6000)  # 企微小预算

        # 企微小预算下应有大量归档
        archived = sum(
            1 for m in msgs
            if m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("[已归档")
        )
        assert archived >= 2, f"企微小预算应归档至少 2 条，实际 {archived}"
