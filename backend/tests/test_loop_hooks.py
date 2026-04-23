"""LoopHook 单元测试 — 6 个 hook 的独立行为验证

每个 hook 单一职责，可独立单测。覆盖：
- ProgressNotifyHook：task_id 缺失时不推送 / 存在时调 stream_publish
- ToolAuditHook：fire-and-forget 写入 tool_audit_log
- TemporalValidatorHook：合成阶段改写文本
- FailureReflectionHook：错误前缀触发 / 业务"错误"字串不触发
- AmbiguityDetectionHook：多条匹配触发 / 单条不触发 / 非目标工具不触发
- SubAgentThinkingHook：子Agent工具进度推送到thinking区域
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.agent.loop_hooks import (
    AmbiguityDetectionHook,
    FailureReflectionHook,
    LoopHook,
    ProgressNotifyHook,
    SubAgentThinkingHook,
    TemporalValidatorHook,
    ToolAuditHook,
)
from services.agent.loop_types import HookContext

# 预 import 以便 patch 能找到（hook 内部为延迟 import）
import services.websocket_manager  # noqa: F401
import schemas.websocket  # noqa: F401
import services.agent.guardrails  # noqa: F401
import services.agent.tool_audit  # noqa: F401


# ════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════

def make_ctx(task_id=None) -> HookContext:
    """构造测试用 HookContext"""
    return HookContext(
        db=MagicMock(),
        user_id="user_zhangsan",
        org_id="org_test",
        conversation_id="conv_001",
        task_id=task_id,
        request_ctx=MagicMock(),
        turn=1,
        messages=[],
        tools_called=[],
        selected_tools=[],
        budget=None,
    )


# ════════════════════════════════════════════════════════
# LoopHook 基类
# ════════════════════════════════════════════════════════

class TestLoopHookBase:
    """LoopHook 基类所有方法都是 no-op default"""

    @pytest.mark.asyncio
    async def test_on_turn_start_noop(self):
        hook = LoopHook()
        await hook.on_turn_start(make_ctx())  # 不抛异常即通过

    @pytest.mark.asyncio
    async def test_on_tool_start_noop(self):
        hook = LoopHook()
        await hook.on_tool_start(make_ctx(), "test_tool", {"a": 1})

    @pytest.mark.asyncio
    async def test_on_tool_end_noop(self):
        hook = LoopHook()
        await hook.on_tool_end(
            make_ctx(), "test_tool", {"a": 1}, "result",
            "success", 100, False, False, "tc_001",
        )

    @pytest.mark.asyncio
    async def test_on_text_synthesis_returns_input_unchanged(self):
        hook = LoopHook()
        result = await hook.on_text_synthesis(make_ctx(), "原始文本")
        assert result == "原始文本"


# ════════════════════════════════════════════════════════
# ProgressNotifyHook
# ════════════════════════════════════════════════════════

class TestProgressNotifyHook:
    """ProgressNotifyHook：task_id 控制开关"""

    @pytest.mark.asyncio
    async def test_no_task_id_skips_publish(self):
        """task_id=None 时不推送（headless 场景）"""
        hook = ProgressNotifyHook(max_turns=20)
        with patch("services.websocket_manager.ws_manager.send_to_task_or_user") as mock_publish:
            await hook.on_turn_start(make_ctx(task_id=None))
            mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_task_id_calls_publish(self):
        """task_id 存在时应调 stream_publish"""
        hook = ProgressNotifyHook(max_turns=20)
        ctx = make_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user", new_callable=AsyncMock,
        ) as mock_publish, patch(
            "schemas.websocket.build_agent_step", return_value={"msg": "ok"},
        ):
            await hook.on_turn_start(ctx)
            mock_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_exit_signal_tools_skipped(self):
        """退出信号工具（route_to_chat / ask_user）不推送进度"""
        hook = ProgressNotifyHook(max_turns=20)
        ctx = make_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user", new_callable=AsyncMock,
        ) as mock_publish:
            await hook.on_tool_start(ctx, "ask_user", {})
            await hook.on_tool_start(ctx, "route_to_chat", {})
            mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_raise(self):
        """推送失败只 debug 不抛异常（不阻塞主流程）"""
        hook = ProgressNotifyHook(max_turns=20)
        ctx = make_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("ws closed"),
        ):
            await hook.on_turn_start(ctx)  # 不应抛异常


# ════════════════════════════════════════════════════════
# ToolAuditHook
# ════════════════════════════════════════════════════════

class TestToolAuditHook:
    """ToolAuditHook：fire-and-forget 写审计日志"""

    @pytest.mark.asyncio
    async def test_creates_audit_task(self):
        """on_tool_end 应触发 record_tool_audit task"""
        hook = ToolAuditHook()
        ctx = make_ctx(task_id="task_001")

        # Patch asyncio.create_task to capture the call
        with patch(
            "services.agent.tool_audit.record_tool_audit",
            new_callable=AsyncMock,
        ) as mock_record, patch(
            "asyncio.create_task",
        ) as mock_create:
            await hook.on_tool_end(
                ctx, "local_stock_query", {"product": "A"},
                "库存128件", "success", 250, False, False, "tc_001",
            )
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_does_not_raise_on_failure(self):
        """审计构造失败时只 debug 不抛"""
        hook = ToolAuditHook()
        ctx = make_ctx(task_id="task_001")

        with patch(
            "services.agent.tool_audit.build_args_hash",
            side_effect=RuntimeError("hash error"),
        ):
            await hook.on_tool_end(
                ctx, "test_tool", {}, "result",
                "success", 100, False, False, "tc_001",
            )  # 不抛异常


# ════════════════════════════════════════════════════════
# TemporalValidatorHook
# ════════════════════════════════════════════════════════

class TestTemporalValidatorHook:
    """TemporalValidatorHook：L4 时间事实校验"""

    @pytest.mark.asyncio
    async def test_validation_patches_text(self):
        """validate_and_patch 返回的 patched_text 被采用"""
        hook = TemporalValidatorHook()
        ctx = make_ctx()

        with patch(
            "services.agent.guardrails.validate_and_patch",
            return_value=("patched 文本", []),
        ), patch("services.agent.guardrails.emit_deviation_records"):
            result = await hook.on_text_synthesis(ctx, "原始文本")
            assert result == "patched 文本"

    @pytest.mark.asyncio
    async def test_emits_deviations_when_present(self):
        """有 deviation 时应调 emit_deviation_records"""
        hook = TemporalValidatorHook()
        ctx = make_ctx(task_id="task_001")
        deviations = [{"field": "date", "expected": "2026-04-11"}]

        with patch(
            "services.agent.guardrails.validate_and_patch",
            return_value=("patched", deviations),
        ), patch(
            "services.agent.guardrails.emit_deviation_records",
        ) as mock_emit:
            await hook.on_text_synthesis(ctx, "原始")
            mock_emit.assert_called_once()
            assert mock_emit.call_args.kwargs["patched"] is True

    @pytest.mark.asyncio
    async def test_validator_failure_returns_original(self):
        """校验函数异常时返回原始文本（fail-open）"""
        hook = TemporalValidatorHook()
        ctx = make_ctx()

        with patch(
            "services.agent.guardrails.validate_and_patch",
            side_effect=RuntimeError("validator broken"),
        ):
            result = await hook.on_text_synthesis(ctx, "原始文本")
            assert result == "原始文本"


# ════════════════════════════════════════════════════════
# FailureReflectionHook
# ════════════════════════════════════════════════════════

class TestFailureReflectionHook:
    """FailureReflectionHook：[A2] 工具错误时注入分析提示"""

    @pytest.mark.asyncio
    async def test_error_prefix_triggers_injection(self):
        """工具执行失败前缀触发系统消息注入"""
        hook = FailureReflectionHook()
        ctx = make_ctx()

        await hook.on_tool_end(
            ctx, "local_stock_query", {"a": 1},
            "工具执行失败: 数据库连接超时",
            "error", 5000, False, False, "tc_001",
        )
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "system"
        assert "local_stock_query" in ctx.messages[0]["content"]
        assert "ask_user" in ctx.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_timeout_prefix_triggers_injection(self):
        """超时也触发"""
        hook = FailureReflectionHook()
        ctx = make_ctx()

        await hook.on_tool_end(
            ctx, "erp_query", {},
            "工具执行超时（30秒），请缩小查询范围",
            "timeout", 30000, False, False, "tc_001",
        )
        assert len(ctx.messages) == 1

    @pytest.mark.asyncio
    async def test_business_error_string_does_not_trigger(self):
        """业务数据中包含'错误'/'失败'不触发（避免误报）"""
        hook = FailureReflectionHook()
        ctx = make_ctx()

        # 业务正常返回，但内容包含"错误"
        await hook.on_tool_end(
            ctx, "local_stock_query", {},
            "查询成功，库存数据如下：商品A有50件，商品B错误码=0表示正常",
            "success", 200, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0  # 不应注入

    @pytest.mark.asyncio
    async def test_traceback_triggers_injection(self):
        """Traceback 前缀触发"""
        hook = FailureReflectionHook()
        ctx = make_ctx()

        await hook.on_tool_end(
            ctx, "code_execute", {},
            "Traceback (most recent call last):\n  File ...",
            "error", 100, False, False, "tc_001",
        )
        assert len(ctx.messages) == 1

    @pytest.mark.asyncio
    async def test_empty_result_does_not_trigger(self):
        """空结果不触发（无内容可分析）"""
        hook = FailureReflectionHook()
        ctx = make_ctx()

        await hook.on_tool_end(
            ctx, "test_tool", {}, "",
            "success", 100, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0


# ════════════════════════════════════════════════════════
# AmbiguityDetectionHook
# ════════════════════════════════════════════════════════

class TestAmbiguityDetectionHook:
    """AmbiguityDetectionHook：[A1] 多条匹配时注入 ask_user 提示"""

    @pytest.mark.asyncio
    async def test_multi_product_match_triggers(self):
        """local_product_identify 返回多个商品时触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        result = (
            '搜索"蓝色卫衣"匹配到5个商品：\n'
            "1. BH-001 — 蓝色连帽卫衣\n"
            "2. BH-002 — 深蓝色圆领卫衣\n"
            "3. BH-003 — 天蓝色加绒卫衣\n"
            "4. BH-004 — 蓝色短袖卫衣\n"
            "5. BH-005 — 蓝色拼接卫衣"
        )
        await hook.on_tool_end(
            ctx, "local_product_identify", {"name": "蓝色卫衣"},
            result, "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "system"
        assert "ask_user" in ctx.messages[0]["content"]
        assert "5" in ctx.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_multi_sku_match_triggers(self):
        """local_product_identify 返回多个 SKU 时触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        result = (
            '搜索规格"红色"匹配到3个SKU：\n'
            "1. SK-001 — 红色T恤 | 规格: 红色 S码\n"
            "2. SK-002 — 红色T恤 | 规格: 红色 M码\n"
            "3. SK-003 — 红色外套 | 规格: 红色 L码"
        )
        await hook.on_tool_end(
            ctx, "local_product_identify", {"spec": "红色"},
            result, "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 1
        assert "3" in ctx.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_single_match_does_not_trigger(self):
        """精确匹配到1个结果不触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        result = (
            '搜索"蓝色连帽卫衣"匹配到1个商品：\n'
            "1. BH-001 — 蓝色连帽卫衣"
        )
        await hook.on_tool_end(
            ctx, "local_product_identify", {"name": "蓝色连帽卫衣"},
            result, "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0

    @pytest.mark.asyncio
    async def test_exact_code_match_does_not_trigger(self):
        """编码精确识别（无"匹配到N个"格式）不触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        result = (
            "编码识别: BH-001\n"
            "✓ 商品存在 | 编码类型: 主编码(outer_id)\n"
            "名称: 蓝色连帽卫衣"
        )
        await hook.on_tool_end(
            ctx, "local_product_identify", {"code": "BH-001"},
            result, "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0

    @pytest.mark.asyncio
    async def test_non_target_tool_does_not_trigger(self):
        """非目标工具即使结果包含匹配格式也不触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        result = '搜索"蓝色"匹配到10个商品：\n1. ...'
        await hook.on_tool_end(
            ctx, "local_order_query", {"product_code": "A"},
            result, "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0

    @pytest.mark.asyncio
    async def test_empty_result_does_not_trigger(self):
        """空结果不触发"""
        hook = AmbiguityDetectionHook()
        ctx = make_ctx()

        await hook.on_tool_end(
            ctx, "local_product_identify", {"name": "xxx"},
            "", "success", 50, False, False, "tc_001",
        )
        assert len(ctx.messages) == 0


# ════════════════════════════════════════════════════════
# v6: ToolAuditHook token + trace_id 传递
# ════════════════════════════════════════════════════════


class TestToolAuditHookV6:
    """v6: on_tool_end 传递 turn_prompt_tokens/completion_tokens + trace_id"""

    @pytest.mark.asyncio
    async def test_token_and_trace_passed_to_entry(self):
        """turn_prompt_tokens/completion_tokens 传入 ToolAuditEntry"""
        from services.agent.observability import set_trace_id
        set_trace_id("trace_v6_test")

        hook = ToolAuditHook()
        ctx = make_ctx(task_id="task_v6")

        captured_entry = None
        original_create_task = asyncio.create_task

        async def capture_record(db, entry):
            nonlocal captured_entry
            captured_entry = entry

        with patch(
            "services.agent.tool_audit.record_tool_audit",
            new=capture_record,
        ):
            await hook.on_tool_end(
                ctx, "erp_agent", {"query": "test"},
                "result", "success", 300, False, False, "tc_v6",
                turn_prompt_tokens=1500,
                turn_completion_tokens=400,
            )
            # 让 create_task 创建的协程有机会执行
            await asyncio.sleep(0.01)

        assert captured_entry is not None
        assert captured_entry.prompt_tokens == 1500
        assert captured_entry.completion_tokens == 400
        assert captured_entry.trace_id == "trace_v6_test"
        set_trace_id("")

    @pytest.mark.asyncio
    async def test_kwargs_backward_compatible(self):
        """旧调用方式（不传 token）不报错"""
        hook = ToolAuditHook()
        ctx = make_ctx(task_id="task_compat")

        with patch(
            "services.agent.tool_audit.record_tool_audit",
            new_callable=AsyncMock,
        ), patch("asyncio.create_task"):
            # 不传 turn_prompt_tokens — 向后兼容
            await hook.on_tool_end(
                ctx, "local_data", {}, "result",
                "success", 100, False, False, "tc_old",
            )  # 不报错

    @pytest.mark.asyncio
    async def test_other_hooks_accept_kwargs(self):
        """FailureReflectionHook 等接受 **kwargs 不报错"""
        from services.agent.loop_hooks import FailureReflectionHook
        hook = FailureReflectionHook()
        ctx = make_ctx()
        # 传入 v6 新参数，不应报 TypeError
        await hook.on_tool_end(
            ctx, "test", {}, "result", "success", 100, False, False, "tc1",
            turn_prompt_tokens=500, turn_completion_tokens=200,
        )


# ════════════════════════════════════════════════════════
# SubAgentThinkingHook
# ════════════════════════════════════════════════════════

class TestSubAgentThinkingHook:
    """SubAgentThinkingHook：子Agent工具调用进度推送到thinking区域"""

    def _make_hook(self):
        return SubAgentThinkingHook(
            task_id="task_001",
            conversation_id="conv_001",
            message_id="msg_001",
            user_id="user_zhangsan",
        )

    @pytest.mark.asyncio
    async def test_first_tool_start_pushes_title_and_label(self):
        """首次 on_tool_start 推送标题行 + 工具标签"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ) as mock_build:
            await hook.on_tool_start(ctx, "local_data", {})
            mock_build.assert_called_once()
            chunk_text = mock_build.call_args[1].get("chunk") or mock_build.call_args[0][3]
            assert "── ERP Agent ──" in chunk_text
            assert "查询数据" in chunk_text
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_subsequent_tool_start_no_title(self):
        """后续 on_tool_start 只推送工具标签，不重复标题"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ) as mock_build:
            await hook.on_tool_start(ctx, "local_data", {})
            await hook.on_tool_start(ctx, "code_execute", {})
            second_call_chunk = mock_build.call_args_list[1][1].get("chunk") or mock_build.call_args_list[1][0][3]
            assert "── ERP Agent ──" not in second_call_chunk
            assert "执行数据分析" in second_call_chunk

    @pytest.mark.asyncio
    async def test_tool_label_fallback_to_raw_name(self):
        """未在 TOOL_LABEL 中的工具名使用原始名称"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ) as mock_build:
            await hook.on_tool_start(ctx, "unknown_tool_xyz", {})
            chunk_text = mock_build.call_args[1].get("chunk") or mock_build.call_args[0][3]
            assert "unknown_tool_xyz" in chunk_text

    @pytest.mark.asyncio
    async def test_push_done_after_tool_calls(self):
        """有工具调用后 push_done 推送完成标记"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ) as mock_build:
            await hook.on_tool_start(ctx, "local_data", {})
            mock_send.reset_mock()
            mock_build.reset_mock()

            await hook.push_done()
            mock_build.assert_called_once()
            chunk_text = mock_build.call_args[1].get("chunk") or mock_build.call_args[0][3]
            assert "✓ 完成" in chunk_text

    @pytest.mark.asyncio
    async def test_push_done_without_tool_calls_is_noop(self):
        """无工具调用时 push_done 不推送"""
        hook = self._make_hook()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ) as mock_send:
            await hook.push_done()
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_failure_does_not_raise(self):
        """WS 推送失败不抛异常（静默吞掉）"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("ws closed"),
        ), patch(
            "schemas.websocket.build_thinking_chunk",
            side_effect=RuntimeError("build failed"),
        ):
            await hook.on_tool_start(ctx, "local_data", {})  # 不应抛异常
            await hook.push_done()  # push_done 也不应抛异常

    @pytest.mark.asyncio
    async def test_custom_agent_name(self):
        """自定义 agent_name 出现在标题行"""
        hook = SubAgentThinkingHook(
            task_id="t", conversation_id="c", message_id="m",
            user_id="u", agent_name="Custom Agent",
        )
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ) as mock_build:
            await hook.on_tool_start(ctx, "local_data", {})
            chunk_text = mock_build.call_args[1].get("chunk") or mock_build.call_args[0][3]
            assert "── Custom Agent ──" in chunk_text

    def test_tool_label_covers_core_tools(self):
        """TOOL_LABEL 覆盖所有核心工具"""
        expected = {"local_data", "local_compare_stats", "local_stock_query",
                    "local_product_identify", "code_execute"}
        assert expected.issubset(SubAgentThinkingHook.TOOL_LABEL.keys())

    @pytest.mark.asyncio
    async def test_push_done_is_idempotent(self):
        """push_done 多次调用只推送一次（幂等）"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ):
            await hook.on_tool_start(ctx, "local_data", {})
            mock_send.reset_mock()

            await hook.push_done()
            await hook.push_done()  # 第二次调用
            assert mock_send.call_count == 1  # 只推送一次

    @pytest.mark.asyncio
    async def test_collected_text_contains_all_progress(self):
        """collected_text 包含所有推送过的文本（标题+工具+完成）"""
        hook = self._make_hook()
        ctx = make_ctx()

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "schemas.websocket.build_thinking_chunk",
            return_value={"type": "thinking_chunk"},
        ):
            await hook.on_tool_start(ctx, "local_data", {})
            await hook.on_tool_start(ctx, "code_execute", {})
            await hook.push_done()

        text = hook.collected_text
        assert "── ERP Agent ──" in text
        assert "查询数据" in text
        assert "执行数据分析" in text
        assert "✓ 完成" in text

    @pytest.mark.asyncio
    async def test_collected_text_empty_when_no_tools(self):
        """无工具调用时 collected_text 为空"""
        hook = self._make_hook()
        await hook.push_done()
        assert hook.collected_text == ""
