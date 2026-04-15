"""WebSocketManager Steer（用户打断）系统单元测试

覆盖：register/check/resolve/unregister_steer_listener 完整生命周期
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from services.websocket_manager import WebSocketManager


@pytest.fixture
def ws_manager():
    """创建干净的 WebSocketManager 实例（mock Redis）"""
    with patch.object(WebSocketManager, "_init_redis_state"):
        mgr = WebSocketManager()
    return mgr


# ============================================================
# register / unregister
# ============================================================


class TestSteerRegistration:

    def test_register_creates_event(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        assert "task_1" in ws_manager._steer_signals
        assert isinstance(ws_manager._steer_signals["task_1"], asyncio.Event)
        assert not ws_manager._steer_signals["task_1"].is_set()

    def test_unregister_cleans_up(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        ws_manager.unregister_steer_listener("task_1")
        assert "task_1" not in ws_manager._steer_signals
        assert "task_1" not in ws_manager._steer_messages

    def test_unregister_nonexistent_no_error(self, ws_manager):
        """未注册的 task_id 取消注册不报错"""
        ws_manager.unregister_steer_listener("no_such_task")


# ============================================================
# check_steer（非阻塞检查）
# ============================================================


class TestCheckSteer:

    def test_no_steer_returns_none(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        assert ws_manager.check_steer("task_1") is None

    def test_unregistered_task_returns_none(self, ws_manager):
        assert ws_manager.check_steer("no_such_task") is None

    def test_after_resolve_returns_message(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        ws_manager.resolve_steer("task_1", "用户打断消息")
        msg = ws_manager.check_steer("task_1")
        assert msg == "用户打断消息"

    def test_check_consumes_signal(self, ws_manager):
        """check 后信号被消费，再次 check 返回 None"""
        ws_manager.register_steer_listener("task_1")
        ws_manager.resolve_steer("task_1", "打断")
        ws_manager.check_steer("task_1")
        assert ws_manager.check_steer("task_1") is None


# ============================================================
# resolve_steer（前端触发）
# ============================================================


class TestResolveSteer:

    def test_resolve_with_listener(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        result = ws_manager.resolve_steer("task_1", "我想改查别的")
        assert result is True
        assert ws_manager._steer_signals["task_1"].is_set()

    def test_resolve_without_listener(self, ws_manager):
        result = ws_manager.resolve_steer("no_task", "消息")
        assert result is False

    def test_resolve_stores_message(self, ws_manager):
        ws_manager.register_steer_listener("task_1")
        ws_manager.resolve_steer("task_1", "新消息内容")
        assert ws_manager._steer_messages["task_1"] == "新消息内容"


# ============================================================
# 完整生命周期
# ============================================================


class TestSteerLifecycle:

    def test_full_lifecycle(self, ws_manager):
        """register → resolve → check → unregister 完整流程"""
        ws_manager.register_steer_listener("t1")
        assert ws_manager.check_steer("t1") is None

        ws_manager.resolve_steer("t1", "打断了")
        msg = ws_manager.check_steer("t1")
        assert msg == "打断了"

        ws_manager.unregister_steer_listener("t1")
        assert "t1" not in ws_manager._steer_signals
        assert "t1" not in ws_manager._steer_messages

    def test_multiple_tasks_isolated(self, ws_manager):
        """多个 task 的 steer 信号互不干扰"""
        ws_manager.register_steer_listener("t1")
        ws_manager.register_steer_listener("t2")

        ws_manager.resolve_steer("t1", "打断t1")

        assert ws_manager.check_steer("t1") == "打断t1"
        assert ws_manager.check_steer("t2") is None
