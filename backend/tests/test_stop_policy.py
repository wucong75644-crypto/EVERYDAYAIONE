"""
Agent 四态停止策略单元测试

覆盖：
- ResultClass 分类器（classify_tool_result）：结构化信号优先 + 关键词 fallback
- StopDecision 决策器（evaluate）：8 种决策场景
- most_severe：多工具并行取最严重
- FailureTracker：连续失败追踪 + 签名匹配 + 重置
- _slim_messages_for_synthesis：system 消息收集
- build_synthesis_context：content_blocks + files 提取
- StopPolicyConfig：allow_ask_user=False 降级
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass

from services.agent.stop_policy import (
    ResultClass,
    StopDecision,
    classify_tool_result,
    evaluate,
    most_severe,
    FailureTracker,
    StopPolicyConfig,
    build_synthesis_context,
    _slim_messages_for_synthesis,
    _classify_error_text,
    synthesize_wrap_up,
)


# ============================================================
# classify_tool_result
# ============================================================

class TestClassifyToolResult:
    """结构化信号优先 + 关键词 fallback"""

    def test_audit_success_returns_success(self):
        """audit_status=success → SUCCESS，不看 result"""
        assert classify_tool_result("任何内容", "success") == ResultClass.SUCCESS

    def test_audit_timeout_returns_retryable(self):
        """audit_status=timeout → RETRYABLE"""
        assert classify_tool_result("超时了", "timeout") == ResultClass.RETRYABLE

    def test_agent_result_success(self):
        """AgentResult(status=success) → SUCCESS"""
        from services.agent.agent_result import AgentResult
        r = AgentResult(summary="OK", status="success")
        assert classify_tool_result(r, "error") == ResultClass.SUCCESS

    def test_agent_result_ask_user(self):
        """AgentResult(status=ask_user) → NEEDS_INPUT"""
        from services.agent.agent_result import AgentResult
        r = AgentResult(summary="需要确认", status="ask_user")
        assert classify_tool_result(r, "error") == ResultClass.NEEDS_INPUT

    def test_agent_result_timeout(self):
        """AgentResult(status=timeout, is_failure=True) → RETRYABLE"""
        from services.agent.agent_result import AgentResult
        r = AgentResult(summary="超时", status="timeout")
        assert classify_tool_result(r, "error") == ResultClass.RETRYABLE

    def test_agent_result_error_with_fatal_keyword(self):
        """AgentResult(status=error, error_message 含权限关键词) → FATAL"""
        from services.agent.agent_result import AgentResult
        r = AgentResult(
            summary="失败", status="error",
            error_message="403 Forbidden: 权限不足",
        )
        assert classify_tool_result(r, "error") == ResultClass.FATAL

    def test_agent_result_error_generic(self):
        """AgentResult(status=error, error_message 无特殊关键词) → RETRYABLE"""
        from services.agent.agent_result import AgentResult
        r = AgentResult(
            summary="失败", status="error",
            error_message="连接被重置",
        )
        assert classify_tool_result(r, "error") == ResultClass.RETRYABLE

    def test_string_error_with_needs_input_keyword(self):
        """纯字符串 + audit_status=error + 含缺少关键词 → NEEDS_INPUT"""
        assert classify_tool_result(
            "缺少必要参数: time_range", "error",
        ) == ResultClass.NEEDS_INPUT

    def test_string_error_with_ambiguous_keyword(self):
        """纯字符串 + audit_status=error + 含多候选关键词 → AMBIGUOUS"""
        assert classify_tool_result(
            "匹配到多个商品，哪一个是目标", "error",
        ) == ResultClass.AMBIGUOUS

    def test_string_success_not_classified_as_error(self):
        """纯字符串 + audit_status=success → SUCCESS（不走关键词）"""
        assert classify_tool_result(
            "权限不足（这是数据内容不是错误）", "success",
        ) == ResultClass.SUCCESS

    def test_unknown_result_type_defaults_retryable(self):
        """未知类型 + audit_status=error → RETRYABLE"""
        assert classify_tool_result(12345, "error") == ResultClass.RETRYABLE


# ============================================================
# _classify_error_text
# ============================================================

class TestClassifyErrorText:
    """关键词 fallback 分类"""

    def test_empty_text_returns_retryable(self):
        assert _classify_error_text("") == ResultClass.RETRYABLE

    def test_fatal_patterns(self):
        assert _classify_error_text("permission denied") == ResultClass.FATAL
        assert _classify_error_text("HTTP 403 forbidden") == ResultClass.FATAL
        assert _classify_error_text("工具不存在") == ResultClass.FATAL

    def test_needs_input_patterns(self):
        assert _classify_error_text("缺少参数 time_range") == ResultClass.NEEDS_INPUT
        assert _classify_error_text("missing required field") == ResultClass.NEEDS_INPUT

    def test_ambiguous_patterns(self):
        assert _classify_error_text("匹配到3个候选商品") == ResultClass.AMBIGUOUS
        assert _classify_error_text("multiple results found") == ResultClass.AMBIGUOUS

    def test_no_match_returns_retryable(self):
        assert _classify_error_text("连接超时请重试") == ResultClass.RETRYABLE

    def test_fatal_takes_priority_over_needs_input(self):
        """同时含 fatal 和 needs_input 关键词 → FATAL（优先级更高）"""
        assert _classify_error_text("permission denied, 请提供凭证") == ResultClass.FATAL


# ============================================================
# FailureTracker
# ============================================================

class TestFailureTracker:

    def test_initial_state(self):
        t = FailureTracker()
        assert t.consecutive_failures == 0
        assert t.same_error_streak == 0
        assert t.total_failures == 0
        assert not t.has_meaningful_progress

    def test_record_success_resets_and_marks_progress(self):
        t = FailureTracker()
        t.record_failure("tool_a", "error 1")
        t.record_failure("tool_a", "error 1")
        t.record_success()
        assert t.consecutive_failures == 0
        assert t.same_error_streak == 0
        assert t.last_error_signature == ""
        assert t.has_meaningful_progress is True
        # total_failures 不重置
        assert t.total_failures == 2

    def test_record_failure_increments(self):
        t = FailureTracker()
        t.record_failure("tool_a", "timeout")
        assert t.consecutive_failures == 1
        assert t.total_failures == 1
        assert t.same_error_streak == 1

    def test_same_error_streak_detects_same_tool_same_prefix(self):
        """同一工具 + 相同错误前缀 → same_error_streak 递增"""
        t = FailureTracker()
        t.record_failure("erp_agent", "403 Forbidden: 权限不足")
        t.record_failure("erp_agent", "403 Forbidden: 权限不足")
        assert t.same_error_streak == 2
        assert t.consecutive_failures == 2

    def test_different_error_resets_streak(self):
        """不同错误前缀 → same_error_streak 重置为 1"""
        t = FailureTracker()
        t.record_failure("erp_agent", "403 Forbidden")
        t.record_failure("erp_agent", "Connection timeout")
        assert t.same_error_streak == 1
        assert t.consecutive_failures == 2

    def test_different_tool_resets_streak(self):
        """不同工具 → 签名不同 → streak 重置"""
        t = FailureTracker()
        t.record_failure("tool_a", "timeout")
        t.record_failure("tool_b", "timeout")
        assert t.same_error_streak == 1
        assert t.consecutive_failures == 2


# ============================================================
# most_severe
# ============================================================

class TestMostSevere:

    def test_empty_list_returns_success(self):
        assert most_severe([]) == ResultClass.SUCCESS

    def test_single_element(self):
        assert most_severe([ResultClass.FATAL]) == ResultClass.FATAL

    def test_mixed_returns_worst(self):
        """SUCCESS + RETRYABLE + FATAL → FATAL"""
        assert most_severe([
            ResultClass.SUCCESS,
            ResultClass.RETRYABLE,
            ResultClass.FATAL,
        ]) == ResultClass.FATAL

    def test_all_success(self):
        assert most_severe([
            ResultClass.SUCCESS, ResultClass.SUCCESS,
        ]) == ResultClass.SUCCESS

    def test_needs_input_beats_ambiguous(self):
        assert most_severe([
            ResultClass.AMBIGUOUS,
            ResultClass.NEEDS_INPUT,
        ]) == ResultClass.NEEDS_INPUT


# ============================================================
# evaluate
# ============================================================

class TestEvaluate:
    """8 种决策场景"""

    def _cfg(self, **kw) -> StopPolicyConfig:
        return StopPolicyConfig(**kw)

    def test_success_returns_continue(self):
        t = FailureTracker()
        assert evaluate(t, ResultClass.SUCCESS, self._cfg(), 10) == StopDecision.CONTINUE

    def test_fatal_no_progress_returns_ask_user(self):
        """FATAL + 无进展 + allow_ask_user=True → ASK_USER"""
        t = FailureTracker()
        assert evaluate(t, ResultClass.FATAL, self._cfg(), 10) == StopDecision.ASK_USER

    def test_fatal_has_progress_returns_wrap_up(self):
        """FATAL + 有进展 → WRAP_UP"""
        t = FailureTracker()
        t.has_meaningful_progress = True
        assert evaluate(t, ResultClass.FATAL, self._cfg(), 10) == StopDecision.WRAP_UP

    def test_needs_input_returns_ask_user(self):
        t = FailureTracker()
        assert evaluate(t, ResultClass.NEEDS_INPUT, self._cfg(), 10) == StopDecision.ASK_USER

    def test_ambiguous_returns_ask_user(self):
        t = FailureTracker()
        assert evaluate(t, ResultClass.AMBIGUOUS, self._cfg(), 10) == StopDecision.ASK_USER

    def test_retryable_first_time_returns_continue(self):
        """可重试 + 首次失败 → CONTINUE"""
        t = FailureTracker()
        t.consecutive_failures = 1
        t.same_error_streak = 1
        assert evaluate(t, ResultClass.RETRYABLE, self._cfg(), 10) == StopDecision.CONTINUE

    def test_same_error_streak_exceeds_limit_returns_ask(self):
        """同类错误超限 → ASK_USER"""
        t = FailureTracker()
        t.same_error_streak = 2  # > max_same_error_retries=1
        t.consecutive_failures = 2
        assert evaluate(t, ResultClass.RETRYABLE, self._cfg(), 10) == StopDecision.ASK_USER

    def test_consecutive_failures_reach_wrap_threshold(self):
        """连续失败 ≥3 → WRAP_UP"""
        t = FailureTracker()
        t.consecutive_failures = 3  # ≥ max_consecutive_for_wrap=3
        t.same_error_streak = 1
        assert evaluate(t, ResultClass.RETRYABLE, self._cfg(), 10) == StopDecision.WRAP_UP

    def test_consecutive_failures_reach_ask_threshold(self):
        """连续失败 ≥2 但 <3 → ASK_USER"""
        t = FailureTracker()
        t.consecutive_failures = 2  # ≥ max_consecutive_for_ask=2
        t.same_error_streak = 1
        assert evaluate(t, ResultClass.RETRYABLE, self._cfg(), 10) == StopDecision.ASK_USER

    def test_turns_remaining_triggers_wrap_up(self):
        """剩余轮次 ≤ 预留 → WRAP_UP"""
        t = FailureTracker()
        t.consecutive_failures = 1
        t.same_error_streak = 1
        assert evaluate(t, ResultClass.RETRYABLE, self._cfg(), turns_remaining=1) == StopDecision.WRAP_UP

    def test_allow_ask_user_false_degrades_to_wrap_up(self):
        """allow_ask_user=False → ASK_USER 降级为 WRAP_UP"""
        t = FailureTracker()
        cfg = self._cfg(allow_ask_user=False)
        assert evaluate(t, ResultClass.NEEDS_INPUT, cfg, 10) == StopDecision.WRAP_UP

    def test_allow_ask_user_false_fatal_no_progress(self):
        """allow_ask_user=False + FATAL + 无进展 → WRAP_UP（降级）"""
        t = FailureTracker()
        cfg = self._cfg(allow_ask_user=False)
        assert evaluate(t, ResultClass.FATAL, cfg, 10) == StopDecision.WRAP_UP


# ============================================================
# _slim_messages_for_synthesis
# ============================================================

class TestSlimMessages:

    def test_short_messages_not_truncated(self):
        """消息数 ≤ 阈值 → 不截断"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查数据"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _slim_messages_for_synthesis(msgs, keep_recent=3)
        assert len(result) == 3

    def test_collects_all_system_messages(self):
        """收集所有 system 消息，包括中间 Hook 注入的"""
        msgs = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "查数据"},
            {"role": "assistant", "content": "调用工具"},
            {"role": "tool", "content": "结果1"},
            {"role": "system", "content": "工具 X 返回了错误：超时"},  # Hook 注入
            {"role": "assistant", "content": "重试"},
            {"role": "tool", "content": "结果2"},
            {"role": "system", "content": "工具 Y 返回了错误：403"},  # Hook 注入
            {"role": "assistant", "content": "最终回答"},
            {"role": "tool", "content": "结果3"},
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好"},
        ]
        result = _slim_messages_for_synthesis(msgs, keep_recent=2)
        system_msgs = [m for m in result if m["role"] == "system"]
        # 3 条 system 消息全部保留
        assert len(system_msgs) == 3
        assert "系统提示" in system_msgs[0]["content"]
        assert "超时" in system_msgs[1]["content"]
        assert "403" in system_msgs[2]["content"]

    def test_keeps_recent_non_system(self):
        """最近 N 轮非 system 消息保留"""
        msgs = [
            {"role": "system", "content": "SP"},
            {"role": "user", "content": "早期消息"},
            {"role": "assistant", "content": "早期回答"},
            {"role": "tool", "content": "早期结果"},
            {"role": "user", "content": "最近消息1"},
            {"role": "assistant", "content": "最近回答1"},
            {"role": "user", "content": "最近消息2"},
            {"role": "assistant", "content": "最近回答2"},
        ]
        result = _slim_messages_for_synthesis(msgs, keep_recent=2)
        non_system = [m for m in result if m["role"] != "system"]
        # keep_recent=2 → 最后 4 条非 system
        assert len(non_system) == 4
        assert non_system[0]["content"] == "最近消息1"


# ============================================================
# build_synthesis_context
# ============================================================

class TestBuildSynthesisContext:

    def test_empty_inputs_returns_empty(self):
        assert build_synthesis_context() == ""

    def test_extracts_tool_steps(self):
        blocks = [
            {"type": "tool_step", "tool_name": "erp_agent", "status": "success", "result": "找到5条订单"},
            {"type": "text", "text": "一些文字"},  # 非 tool_step 跳过
            {"type": "tool_step", "tool_name": "code_execute", "status": "error", "result": "语法错误"},
        ]
        ctx = build_synthesis_context(content_blocks=blocks)
        assert "erp_agent(success)" in ctx
        assert "code_execute(error)" in ctx
        assert "一些文字" not in ctx

    def test_extracts_collected_files(self):
        files = [
            {"name": "report.xlsx", "mime_type": "application/xlsx"},
            {"name": "chart.png", "mime_type": "image/png"},
        ]
        ctx = build_synthesis_context(collected_files=files)
        assert "report.xlsx" in ctx
        assert "chart.png" in ctx

    def test_combined_output(self):
        blocks = [{"type": "tool_step", "tool_name": "t1", "status": "ok", "result": "r"}]
        files = [{"name": "f.csv", "mime_type": "text/csv"}]
        ctx = build_synthesis_context(content_blocks=blocks, collected_files=files)
        assert "工具执行记录" in ctx
        assert "已生成的文件" in ctx


# ============================================================
# synthesize_wrap_up
# ============================================================

class TestSynthesizeWrapUp:

    @pytest.mark.asyncio
    async def test_returns_text_on_success(self):
        """正常合成 → 返回文本"""
        @dataclass
        class FakeChunk:
            content: str = ""

        async def fake_stream(**kw):
            yield FakeChunk(content="总结：已完成3项查询。")

        adapter = MagicMock()
        adapter.stream_chat = fake_stream

        result = await synthesize_wrap_up(
            adapter=adapter,
            messages=[{"role": "user", "content": "查数据"}],
            reason="wrap_up_budget",
        )
        assert result == "总结：已完成3项查询。"

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self):
        """空响应 → 返回 None"""
        @dataclass
        class FakeChunk:
            content: str = ""

        async def fake_stream(**kw):
            yield FakeChunk(content="")

        adapter = MagicMock()
        adapter.stream_chat = fake_stream

        result = await synthesize_wrap_up(
            adapter=adapter,
            messages=[{"role": "user", "content": "查数据"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """adapter 抛异常 → 返回 None（不传播）"""
        async def failing_stream(**kw):
            raise RuntimeError("API down")
            yield  # noqa: make it a generator

        adapter = MagicMock()
        adapter.stream_chat = failing_stream

        result = await synthesize_wrap_up(
            adapter=adapter,
            messages=[{"role": "user", "content": "查数据"}],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """超时 → 返回 None"""
        import asyncio

        @dataclass
        class FakeChunk:
            content: str = ""

        async def slow_stream(**kw):
            await asyncio.sleep(10)  # 远超超时
            yield FakeChunk(content="不应该到达这里")

        adapter = MagicMock()
        adapter.stream_chat = slow_stream

        result = await synthesize_wrap_up(
            adapter=adapter,
            messages=[{"role": "user", "content": "查数据"}],
            timeout=0.1,  # 100ms 超时
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_tools_passed_to_adapter(self):
        """确认调用 adapter 时 tools=None"""
        calls = []

        @dataclass
        class FakeChunk:
            content: str = ""

        async def capture_stream(**kw):
            calls.append(kw)
            yield FakeChunk(content="结果")

        adapter = MagicMock()
        adapter.stream_chat = capture_stream

        await synthesize_wrap_up(
            adapter=adapter,
            messages=[{"role": "user", "content": "test"}],
        )
        assert len(calls) == 1
        assert calls[0]["tools"] is None


# ============================================================
# StopPolicyConfig 配置
# ============================================================

class TestStopPolicyConfig:

    def test_default_values(self):
        cfg = StopPolicyConfig()
        assert cfg.allow_ask_user is True
        assert cfg.max_same_error_retries == 1
        assert cfg.max_consecutive_for_ask == 2
        assert cfg.max_consecutive_for_wrap == 3
        assert cfg.wrap_up_turns_reserved == 1

    def test_scheduled_task_config(self):
        """ScheduledTaskAgent 配置：无交互 + 低容忍度"""
        cfg = StopPolicyConfig(
            allow_ask_user=False,
            max_consecutive_for_wrap=2,
        )
        assert cfg.allow_ask_user is False
        assert cfg.max_consecutive_for_wrap == 2

    def test_frozen_dataclass(self):
        """frozen=True → 不可修改"""
        cfg = StopPolicyConfig()
        with pytest.raises(AttributeError):
            cfg.allow_ask_user = False
