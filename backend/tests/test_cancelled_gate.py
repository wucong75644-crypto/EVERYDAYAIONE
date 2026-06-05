"""WebSocket cancelled gate（Phase 1）单元测试

覆盖：
- mark_cancelled_gate + is_in_cancelled_gate 基本功能
- 多租户复合 key 隔离
- TTL 过期自动失效
- cancel_task 同时 set Event 和 mark gate
- send_to_task_or_user / send_to_task_subscribers 闸门检查

设计参考 docs/document/TECH_用户中断与恢复机制.md §13.2 / §四.5
"""

import asyncio
import time

import pytest

from services.cancel_gate import CANCELLED_GATE_TTL
from services.websocket_manager import WebSocketManager


@pytest.fixture
def manager():
    """每个测试用例新建独立 manager（避免全局污染）"""
    return WebSocketManager()


class TestMarkCancelledGate:
    """mark_cancelled_gate — 基础写入"""

    @pytest.mark.asyncio
    async def test_mark_writes_composite_key(self, manager):
        await manager.mark_cancelled_gate("task_A", org_id="org_x")
        assert ("org_x", "task_A") in manager._cancel._gates

    @pytest.mark.asyncio
    async def test_mark_default_org_empty_string(self, manager):
        await manager.mark_cancelled_gate("task_A")
        assert ("", "task_A") in manager._cancel._gates

    @pytest.mark.asyncio
    async def test_mark_ttl_set_correctly(self, manager):
        before = time.time()
        await manager.mark_cancelled_gate("task_A", "org_x")
        expire_at = manager._cancel._gates[("org_x", "task_A")]
        assert before + CANCELLED_GATE_TTL - 1 <= expire_at <= time.time() + CANCELLED_GATE_TTL + 1


class TestIsInCancelledGate:
    """is_in_cancelled_gate — 复合 key 匹配"""

    @pytest.mark.asyncio
    async def test_exact_match(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        assert manager.is_in_cancelled_gate("task_A", "org_x") is True

    @pytest.mark.asyncio
    async def test_cross_org_isolation(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        assert manager.is_in_cancelled_gate("task_A", "org_y") is False

    @pytest.mark.asyncio
    async def test_not_in_gate(self, manager):
        assert manager.is_in_cancelled_gate("task_NEVER", "org_x") is False

    @pytest.mark.asyncio
    async def test_org_none_matches_any(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        assert manager.is_in_cancelled_gate("task_A", org_id=None) is True

    @pytest.mark.asyncio
    async def test_expired_gate_not_match(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        manager._cancel._gates[("org_x", "task_A")] = time.time() - 1
        assert manager.is_in_cancelled_gate("task_A", "org_x") is False


class TestCleanupCancelledGates:
    """cleanup_cancelled_gates — TTL 清理"""

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        await manager.mark_cancelled_gate("task_B", "org_y")
        manager._cancel._gates[("org_x", "task_A")] = time.time() - 10

        removed = await manager.cleanup_cancelled_gates()

        assert removed == 1
        assert ("org_x", "task_A") not in manager._cancel._gates
        assert ("org_y", "task_B") in manager._cancel._gates

    @pytest.mark.asyncio
    async def test_cleanup_empty(self, manager):
        removed = await manager.cleanup_cancelled_gates()
        assert removed == 0

    @pytest.mark.asyncio
    async def test_cleanup_nothing_expired(self, manager):
        await manager.mark_cancelled_gate("task_A", "org_x")
        removed = await manager.cleanup_cancelled_gates()
        assert removed == 0
        assert len(manager._cancel._gates) == 1


class TestCancelTaskIntegration:
    """cancel_task 同时 set Event 和 mark gate"""

    @pytest.mark.asyncio
    async def test_cancel_task_marks_gate(self, manager):
        manager.register_cancel_listener("task_A")
        manager.cancel_task("task_A", org_id="org_x")
        await asyncio.sleep(0.05)
        assert manager.is_in_cancelled_gate("task_A", "org_x") is True

    @pytest.mark.asyncio
    async def test_cancel_task_sets_asyncio_event(self, manager):
        manager.register_cancel_listener("task_A")
        manager.cancel_task("task_A", org_id="org_x")
        assert manager.is_cancelled("task_A") is True

    @pytest.mark.asyncio
    async def test_cancel_task_without_listener_still_marks_gate(self, manager):
        result = manager.cancel_task("task_NEVER", org_id="org_x")
        assert result is False
        await asyncio.sleep(0.05)
        assert manager.is_in_cancelled_gate("task_NEVER", "org_x") is True

    @pytest.mark.asyncio
    async def test_cancel_task_default_org_backward_compat(self, manager):
        manager.register_cancel_listener("task_A")
        manager.cancel_task("task_A")
        await asyncio.sleep(0.05)
        assert manager.is_in_cancelled_gate("task_A", org_id=None) is True


class TestSendDropsCancelled:
    """send_to_task_or_user / send_to_task_subscribers 闸门检查"""

    @pytest.mark.asyncio
    async def test_send_to_task_or_user_drops_cancelled(self, manager, monkeypatch):
        published_count = [0]

        async def fake_publish(*args, **kwargs):
            published_count[0] += 1

        monkeypatch.setattr(manager, "_publish", fake_publish)

        await manager.mark_cancelled_gate("task_A", "org_x")
        await manager.send_to_task_or_user(
            task_id="task_A",
            user_id="user_1",
            message={"type": "tool_step"},
            org_id="org_x",
        )

        assert published_count[0] == 0

    @pytest.mark.asyncio
    async def test_send_to_task_subscribers_drops_cancelled(self, manager, monkeypatch):
        published_count = [0]

        async def fake_publish(*args, **kwargs):
            published_count[0] += 1

        monkeypatch.setattr(manager, "_publish", fake_publish)

        await manager.mark_cancelled_gate("task_A", "org_x")
        result = await manager.send_to_task_subscribers(
            task_id="task_A",
            message={"type": "tool_step"},
            org_id="org_x",
        )

        assert result == 0
        assert published_count[0] == 0

    @pytest.mark.asyncio
    async def test_send_non_cancelled_task_passes(self, manager, monkeypatch):
        published_count = [0]

        async def fake_publish(*args, **kwargs):
            published_count[0] += 1

        monkeypatch.setattr(manager, "_publish", fake_publish)

        await manager.send_to_task_or_user(
            task_id="task_FRESH",
            user_id="user_1",
            message={"type": "tool_step"},
            org_id="org_x",
        )

        assert published_count[0] == 1

    @pytest.mark.asyncio
    async def test_send_org_isolation(self, manager, monkeypatch):
        published_count = [0]

        async def fake_publish(*args, **kwargs):
            published_count[0] += 1

        monkeypatch.setattr(manager, "_publish", fake_publish)

        await manager.mark_cancelled_gate("task_A", "org_x")

        await manager.send_to_task_or_user(
            task_id="task_A",
            user_id="user_1",
            message={"type": "tool_step"},
            org_id="org_y",
        )

        assert published_count[0] == 1
