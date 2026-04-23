"""
ToolLoopExecutor 并行执行单元测试

覆盖：_execute_tools 三阶段重构后的行为
- 阶段 1 预处理：exit_hit 短路、JSON 解析失败、DANGEROUS 拦截、参数校验
- 阶段 2 并行执行：单工具快路径、多工具 gather、异常安全包裹
- 阶段 3 后处理：结果顺序保持、steer 打断（idx 索引）、tool_expansion 注入
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.loop_types import (
    HookContext, LoopConfig, LoopStrategy,
)
from services.agent.tool_loop_executor import ToolLoopExecutor
from services.agent.tool_result_cache import ToolResultCache


# ============================================================
# 测试辅助
# ============================================================

def _make_hook_ctx(**overrides) -> HookContext:
    defaults = dict(
        db=MagicMock(),
        user_id="u1",
        org_id="o1",
        conversation_id="c1",
        task_id=None,
        request_ctx=MagicMock(),
        messages=[],
        tools_called=[],
        selected_tools=[],
        budget=MagicMock(is_tight=False, tool_timeout=lambda d: d),
    )
    defaults.update(overrides)
    return HookContext(**defaults)


def _make_executor(tools_for_parallel: int = 1) -> ToolLoopExecutor:
    """构造最小 ToolLoopExecutor（跳过 LLM 只测工具执行）"""
    adapter = AsyncMock()
    executor = AsyncMock()
    config = LoopConfig(max_turns=5, max_tokens=50000, tool_timeout=30.0)
    strategy = LoopStrategy(
        exit_signals=frozenset({"route_to_chat", "ask_user"}),
        enable_tool_expansion=True,
    )
    tle = ToolLoopExecutor(
        adapter=adapter,
        executor=executor,
        all_tools=[],
        config=config,
        strategy=strategy,
        hooks=[],
    )
    return tle


def _tc(tool_id: str, name: str, args: str = "{}") -> Dict[str, Any]:
    """构造 tool_call dict"""
    return {"id": tool_id, "name": name, "arguments": args}


# ============================================================
# 阶段 1 预处理
# ============================================================


class TestPhase1Preprocess:
    """_execute_tools 阶段1：退出信号短路 / JSON解析 / 安全检查 / 参数校验"""

    @pytest.mark.asyncio
    async def test_exit_signal_shortcircuits(self):
        """退出信号工具 → 不执行任何工具，直接返回"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        completed = [
            _tc("tc1", "ask_user", '{"message": "请确认"}'),
            _tc("tc2", "local_data", '{"doc_type": "order"}'),
        ]

        result = await tle._execute_tools(
            completed, [], "思考文本", hook_ctx,
        )

        # ask_user 命中退出信号，local_data 不执行
        assert result == "请确认"
        # messages 只有 assistant + tool(OK)，没有 local_data 的结果
        tool_msgs = [m for m in hook_ctx.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "OK"

    @pytest.mark.asyncio
    async def test_bad_json_returns_error(self):
        """JSON 解析失败 → 错误信息回灌，不执行工具"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        completed = [_tc("tc1", "local_data", "not valid json!!!")]

        result = await tle._execute_tools(
            completed, [], "", hook_ctx,
        )

        assert "JSON格式错误" in result

    @pytest.mark.asyncio
    async def test_empty_ready_after_all_filtered(self):
        """所有工具都在预处理中被过滤 → 不进入并行执行"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        # 两个工具：一个 exit_signal，一个 bad json
        completed = [
            _tc("tc1", "route_to_chat", "{}"),
        ]

        result = await tle._execute_tools(
            completed, [], "转到聊天", hook_ctx,
        )

        # route_to_chat → accumulated = turn_text
        assert result == "转到聊天"


# ============================================================
# 阶段 2 并行执行
# ============================================================


class TestPhase2ParallelExecution:
    """_execute_tools 阶段2：单工具快路径 vs 多工具 gather"""

    @pytest.mark.asyncio
    async def test_single_tool_fast_path(self):
        """单工具 → 不走 gather，直接 await"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        # mock invoke_tool_with_cache
        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            new_callable=AsyncMock,
            return_value=("订单数据: 10条", "success", False, 100),
        ) as mock_invoke:
            completed = [_tc("tc1", "local_data", '{"doc_type":"order"}')]
            result = await tle._execute_tools(
                completed, [], "", hook_ctx,
            )

        mock_invoke.assert_awaited_once()
        assert "订单数据" in result

    @pytest.mark.asyncio
    async def test_multiple_tools_parallel(self):
        """多工具 → asyncio.gather 并行执行"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        call_order = []

        async def mock_invoke(executor, cache, name, args, budget, timeout):
            call_order.append(name)
            await asyncio.sleep(0.01)  # 模拟 IO
            return f"{name}_result", "success", False, 50

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            side_effect=mock_invoke,
        ):
            completed = [
                _tc("tc1", "local_data", '{"doc_type":"order"}'),
                _tc("tc2", "local_stock_query", '{"product_code":"A01"}'),
            ]
            result = await tle._execute_tools(
                completed, [], "", hook_ctx,
            )

        # 两个工具都被调用
        assert "local_data" in call_order
        assert "local_stock_query" in call_order
        # messages 中有两条 tool 结果
        tool_msgs = [m for m in hook_ctx.messages
                     if m.get("role") == "tool" and "result" in m.get("content", "")]
        assert len(tool_msgs) == 2

    @pytest.mark.asyncio
    async def test_parallel_error_contained(self):
        """并行工具中一个失败 → 不影响另一个，失败信息回传"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        async def mock_invoke(executor, cache, name, args, budget, timeout):
            if name == "local_data":
                raise RuntimeError("DB连接超时")
            return "库存100件", "success", False, 50

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            side_effect=mock_invoke,
        ):
            completed = [
                _tc("tc1", "local_data", '{"doc_type":"order"}'),
                _tc("tc2", "local_stock_query", '{"product_code":"A01"}'),
            ]
            result = await tle._execute_tools(
                completed, [], "", hook_ctx,
            )

        # 两条 tool 消息都在 messages 中
        tool_msgs = [m for m in hook_ctx.messages if m.get("role") == "tool"]
        # 排除 assistant 消息
        assert len(tool_msgs) == 2
        contents = [m["content"] for m in tool_msgs]
        # 一个失败、一个成功
        assert any("DB连接超时" in c for c in contents)
        assert any("库存100件" in c for c in contents)


# ============================================================
# 阶段 3 后处理
# ============================================================


class TestPhase3PostProcess:
    """_execute_tools 阶段3：结果保序、steer 打断、tool_expansion"""

    @pytest.mark.asyncio
    async def test_results_ordered_by_input(self):
        """多工具并行后，结果按原始 tool_call 顺序入 messages"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx()

        async def mock_invoke(executor, cache, name, args, budget, timeout):
            # local_stock_query 先完成（模拟更快），但应排在后面
            delay = 0.02 if name == "local_data" else 0.01
            await asyncio.sleep(delay)
            return f"{name}_RESULT", "success", False, 50

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            side_effect=mock_invoke,
        ):
            completed = [
                _tc("tc1", "local_data", '{"doc_type":"order"}'),
                _tc("tc2", "local_stock_query", '{"product_code":"A01"}'),
            ]
            await tle._execute_tools(completed, [], "", hook_ctx)

        # messages 中 tool 结果的顺序应与 completed 一致
        tool_msgs = [m for m in hook_ctx.messages
                     if m.get("role") == "tool"]
        assert "local_data_RESULT" in tool_msgs[0]["content"]
        assert "local_stock_query_RESULT" in tool_msgs[1]["content"]

    @pytest.mark.asyncio
    async def test_steer_skips_remaining_parallel_results(self):
        """并行执行后 steer 到达 → 跳过剩余结果（使用 idx 索引）"""
        tle = _make_executor()
        hook_ctx = _make_hook_ctx(task_id="task-steer-parallel")

        async def mock_invoke(executor, cache, name, args, budget, timeout):
            return f"{name}_ok", "success", False, 50

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            side_effect=mock_invoke,
        ):
            # mock ws_manager.check_steer: 第一个工具后处理时触发
            call_count = 0

            def mock_check_steer(task_id):
                nonlocal call_count
                call_count += 1
                # 第一次检查就触发
                if call_count == 1:
                    return "用户打断了"
                return None

            with patch(
                "services.websocket_manager.ws_manager"
            ) as mock_ws:
                mock_ws.check_steer = mock_check_steer
                completed = [
                    _tc("tc1", "local_data", '{"doc_type":"order"}'),
                    _tc("tc2", "local_stock_query", '{"product_code":"A01"}'),
                    _tc("tc3", "local_shop_list", "{}"),
                ]
                await tle._execute_tools(completed, [], "", hook_ctx)

        # messages 分析
        tool_msgs = [m for m in hook_ctx.messages if m.get("role") == "tool"]
        user_msgs = [m for m in hook_ctx.messages if m.get("role") == "user"]

        # tc1 正常执行（local_data_ok）
        assert "local_data_ok" in tool_msgs[0]["content"]
        # tc2, tc3 被跳过
        skipped = [m for m in tool_msgs if "跳过此工具调用" in m.get("content", "")]
        assert len(skipped) == 2
        # 用户打断消息注入
        assert user_msgs[-1]["content"] == "用户打断了"

    @pytest.mark.asyncio
    async def test_tool_expansion_injects_after_execution(self):
        """执行扩展工具后，inject_tool 将其 Schema 注入 selected_tools"""
        all_tools = [
            {"type": "function", "function": {"name": "local_shop_list", "parameters": {}}},
        ]
        adapter = AsyncMock()
        executor = AsyncMock()
        config = LoopConfig(max_turns=5, max_tokens=50000, tool_timeout=30.0)
        strategy = LoopStrategy(enable_tool_expansion=True)
        tle = ToolLoopExecutor(
            adapter=adapter, executor=executor,
            all_tools=all_tools, config=config,
            strategy=strategy, hooks=[],
        )
        hook_ctx = _make_hook_ctx(org_id="o1")
        selected_tools = []  # 空的，模拟核心工具集不含 local_shop_list

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            new_callable=AsyncMock,
            return_value=("店铺列表数据", "success", False, 50),
        ):
            completed = [_tc("tc1", "local_shop_list", "{}")]
            await tle._execute_tools(
                completed, selected_tools, "", hook_ctx,
            )

        # inject_tool 应将 local_shop_list 注入 selected_tools
        injected_names = {t["function"]["name"] for t in selected_tools}
        assert "local_shop_list" in injected_names


# ============================================================
# on_tool_start hooks 时序
# ============================================================


class TestHooksTiming:
    """并行化后 hooks 行为"""

    @pytest.mark.asyncio
    async def test_on_tool_start_called_before_execution(self):
        """所有工具的 on_tool_start 在执行前触发"""
        hook = MagicMock()
        hook.on_tool_start = AsyncMock()
        hook.on_tool_end = AsyncMock()

        adapter = AsyncMock()
        executor = AsyncMock()
        config = LoopConfig(max_turns=5, max_tokens=50000, tool_timeout=30.0)
        strategy = LoopStrategy()
        tle = ToolLoopExecutor(
            adapter=adapter, executor=executor,
            all_tools=[], config=config,
            strategy=strategy, hooks=[hook],
        )
        hook_ctx = _make_hook_ctx()

        with patch(
            "services.agent.tool_loop_helpers.invoke_tool_with_cache",
            new_callable=AsyncMock,
            return_value=("ok", "success", False, 10),
        ):
            completed = [
                _tc("tc1", "local_data", '{"doc_type":"order"}'),
                _tc("tc2", "local_stock_query", '{"product_code":"A01"}'),
            ]
            await tle._execute_tools(completed, [], "", hook_ctx)

        # on_tool_start 被调用 2 次
        assert hook.on_tool_start.await_count == 2
        # on_tool_end 被调用 2 次
        assert hook.on_tool_end.await_count == 2
