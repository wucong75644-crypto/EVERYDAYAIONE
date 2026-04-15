"""用户打断（Steer）机制单元测试

覆盖：
- WebSocketManager: register/check/resolve/unregister_steer
- 超时后 resolve 是 no-op
- 无注册时 resolve 返回 False
"""

import asyncio
import pytest

from services.websocket_manager import WebSocketManager


class TestSteerMechanism:
    """steer 信号注册/检查/唤醒/清理"""

    @pytest.fixture
    def manager(self):
        return WebSocketManager()

    def test_check_returns_none_when_no_steer(self, manager):
        """无打断信号时返回 None"""
        manager.register_steer_listener("task-1")
        assert manager.check_steer("task-1") is None

    def test_resolve_then_check(self, manager):
        """resolve 后 check 能拿到消息"""
        manager.register_steer_listener("task-1")
        resolved = manager.resolve_steer("task-1", "帮我查库存")
        assert resolved is True

        msg = manager.check_steer("task-1")
        assert msg == "帮我查库存"

    def test_check_consumes_signal(self, manager):
        """check 后信号被消费，再次 check 返回 None"""
        manager.register_steer_listener("task-1")
        manager.resolve_steer("task-1", "查库存")
        manager.check_steer("task-1")  # 第一次消费

        # 第二次应返回 None（信号已消费 + 监听已清理）
        assert manager.check_steer("task-1") is None

    def test_resolve_without_register(self, manager):
        """未注册时 resolve 返回 False"""
        resolved = manager.resolve_steer("task-999", "消息")
        assert resolved is False

    def test_check_unregistered_task(self, manager):
        """未注册的 task 检查返回 None"""
        assert manager.check_steer("task-999") is None

    def test_unregister_cleans_up(self, manager):
        """unregister 清理信号和消息"""
        manager.register_steer_listener("task-1")
        manager.resolve_steer("task-1", "消息")
        manager.unregister_steer_listener("task-1")

        # 清理后 check 返回 None
        assert manager.check_steer("task-1") is None
        # 内部状态干净
        assert "task-1" not in manager._steer_signals
        assert "task-1" not in manager._steer_messages

    def test_multiple_tasks_isolated(self, manager):
        """多个 task 的 steer 互不干扰"""
        manager.register_steer_listener("task-a")
        manager.register_steer_listener("task-b")

        manager.resolve_steer("task-a", "查库存")

        assert manager.check_steer("task-a") == "查库存"
        assert manager.check_steer("task-b") is None

    def test_register_overwrites_previous(self, manager):
        """重复注册覆盖旧信号"""
        manager.register_steer_listener("task-1")
        manager.resolve_steer("task-1", "旧消息")

        # 重新注册（新的 Event，未 set）
        manager.register_steer_listener("task-1")
        assert manager.check_steer("task-1") is None
