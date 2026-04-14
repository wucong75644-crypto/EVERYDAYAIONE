"""工具写操作确认机制单元测试（Phase 3 B5）

覆盖：
- WebSocketManager.wait_for_confirm / resolve_confirm 交互
- 超时自动返回 False
- resolve 未找到的 tool_call_id
- ToolLoopExecutor._request_user_confirm（headless 跳过 / 正常确认 / 拒绝）
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.websocket_manager import WebSocketManager


# ════════════════════════════════════════════════════════
# WebSocketManager confirm 机制
# ════════════════════════════════════════════════════════

class TestWebSocketManagerConfirm:
    """wait_for_confirm + resolve_confirm 配合测试"""

    @pytest.fixture
    def manager(self):
        return WebSocketManager()

    @pytest.mark.asyncio
    async def test_confirm_approved(self, manager):
        """用户确认 → wait_for_confirm 返回 True"""
        async def approve_later():
            await asyncio.sleep(0.05)
            manager.resolve_confirm("tc_001", True)

        asyncio.create_task(approve_later())
        result = await manager.wait_for_confirm("tc_001", timeout=5.0)
        assert result is True
        # 确认后 pending 已清理
        assert "tc_001" not in manager._pending_confirms

    @pytest.mark.asyncio
    async def test_confirm_rejected(self, manager):
        """用户拒绝 → wait_for_confirm 返回 False"""
        async def reject_later():
            await asyncio.sleep(0.05)
            manager.resolve_confirm("tc_002", False)

        asyncio.create_task(reject_later())
        result = await manager.wait_for_confirm("tc_002", timeout=5.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_confirm_timeout(self, manager):
        """超时 → 返回 False"""
        result = await manager.wait_for_confirm("tc_003", timeout=0.1)
        assert result is False
        # 超时后 pending 已清理
        assert "tc_003" not in manager._pending_confirms

    def test_resolve_missing_tool_call_id(self, manager):
        """resolve 不存在的 tool_call_id → 返回 False"""
        result = manager.resolve_confirm("nonexistent", True)
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_after_timeout_is_noop(self, manager):
        """超时后再 resolve → 不报错（幂等）"""
        await manager.wait_for_confirm("tc_004", timeout=0.05)
        # 此时 pending 已清理，resolve 应返回 False 但不报错
        result = manager.resolve_confirm("tc_004", True)
        assert result is False


# ════════════════════════════════════════════════════════
# ToolLoopExecutor._request_user_confirm
# ════════════════════════════════════════════════════════

class TestRequestUserConfirm:
    """_request_user_confirm 方法测试"""

    def _make_executor(self):
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.loop_types import LoopConfig, LoopStrategy
        return ToolLoopExecutor(
            adapter=MagicMock(),
            executor=MagicMock(),
            all_tools=[],
            config=LoopConfig(max_turns=10, max_tokens=10000, tool_timeout=30),
            strategy=LoopStrategy(exit_signals=frozenset()),
        )

    def _make_hook_ctx(self, task_id=None):
        from services.agent.loop_types import HookContext
        return HookContext(
            db=MagicMock(),
            user_id="user_test",
            org_id="org_test",
            conversation_id="conv_test",
            task_id=task_id,
            request_ctx=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_headless_mode_skips_confirm(self):
        """task_id=None（headless）→ 直接放行"""
        executor = self._make_executor()
        ctx = self._make_hook_ctx(task_id=None)
        result = await executor._request_user_confirm(
            "erp_execute", {"action": "test"}, "tc_001", ctx,
        )
        assert result is None  # None = 继续执行

    @pytest.mark.asyncio
    async def test_confirm_approved_returns_none(self):
        """用户确认 → 返回 None（继续执行）"""
        executor = self._make_executor()
        ctx = self._make_hook_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "services.websocket_manager.ws_manager.wait_for_confirm",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await executor._request_user_confirm(
                "erp_execute", {"action": "test"}, "tc_001", ctx,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_confirm_rejected_returns_message(self):
        """用户拒绝 → 返回拒绝提示文本"""
        executor = self._make_executor()
        ctx = self._make_hook_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
        ), patch(
            "services.websocket_manager.ws_manager.wait_for_confirm",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await executor._request_user_confirm(
                "erp_execute", {"action": "test"}, "tc_001", ctx,
            )
            assert result is not None
            assert "拒绝" in result or "超时" in result

    @pytest.mark.asyncio
    async def test_confirm_error_fails_open(self):
        """确认机制异常 → 放行（fail-open）"""
        executor = self._make_executor()
        ctx = self._make_hook_ctx(task_id="task_001")

        with patch(
            "services.websocket_manager.ws_manager.send_to_task_or_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("ws broken"),
        ):
            result = await executor._request_user_confirm(
                "erp_execute", {"action": "test"}, "tc_001", ctx,
            )
            assert result is None  # fail-open
