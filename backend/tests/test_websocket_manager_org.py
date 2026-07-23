"""
WebSocket send_to_user org_id 过滤测试

验证多租户场景下：
- send_to_user(org_id=X) 只发给该 org 的连接
- send_to_user(org_id=None) 只发给个人空间连接
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

ORG_A = "aaaa1111-0000-0000-0000-000000000001"
ORG_B = "bbbb2222-0000-0000-0000-000000000002"


@dataclass
class FakeConnection:
    """模拟 WebSocket 连接"""
    websocket: MagicMock = field(default_factory=MagicMock)
    user_id: str = "user1"
    org_id: Optional[str] = None
    conn_id: str = ""


class TestSendToUserOrgFilter:
    """send_to_user 按 org_id 过滤连接"""

    @pytest.fixture(autouse=True)
    def setup_manager(self):
        from services.websocket_manager import WebSocketManager
        self.manager = WebSocketManager()
        self.manager.send_to_connection = AsyncMock(return_value=True)
        self.manager._publish = AsyncMock()

        # 模拟同一用户有 3 个连接：org_a, org_b, personal
        self.conn_a = FakeConnection(org_id=ORG_A, conn_id="conn_a")
        self.conn_b = FakeConnection(org_id=ORG_B, conn_id="conn_b")
        self.conn_p = FakeConnection(org_id=None, conn_id="conn_p")

        self.manager._connections["user1"] = {
            "conn_a": self.conn_a,
            "conn_b": self.conn_b,
            "conn_p": self.conn_p,
        }

    @pytest.mark.asyncio
    async def test_org_a_only_sends_to_org_a(self):
        """指定 org_id=A 只发给 A 的连接"""
        await self.manager.send_to_user("user1", {"type": "test"}, org_id=ORG_A)
        self.manager.send_to_connection.assert_called_once_with("conn_a", {"type": "test"})

    @pytest.mark.asyncio
    async def test_org_b_only_sends_to_org_b(self):
        """指定 org_id=B 只发给 B 的连接"""
        await self.manager.send_to_user("user1", {"type": "test"}, org_id=ORG_B)
        self.manager.send_to_connection.assert_called_once_with("conn_b", {"type": "test"})

    @pytest.mark.asyncio
    async def test_no_org_sends_to_personal_only(self):
        """org_id=None 只发给个人空间连接。"""
        await self.manager.send_to_user("user1", {"type": "test"})
        self.manager.send_to_connection.assert_called_once_with(
            "conn_p", {"type": "test"},
        )

    @pytest.mark.asyncio
    async def test_nonexistent_org_sends_nothing(self):
        """不存在的 org_id 不发给任何人"""
        await self.manager.send_to_user(
            "user1", {"type": "test"}, org_id="nonexistent",
        )
        self.manager.send_to_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_receives_org_id(self):
        """跨进程 _publish 也传递 org_id"""
        await self.manager.send_to_user("user1", {"type": "test"}, org_id=ORG_A)
        self.manager._publish.assert_called_once_with(
            "user", "user1", {"type": "test"}, org_id=ORG_A,
        )

    @pytest.mark.asyncio
    async def test_task_or_user_publish_receives_org_id(self):
        """Actor 跨进程发布必须保留组织隔离信息。"""
        await self.manager.send_to_task_or_user(
            "task-1", "user1", {"type": "message_chunk"}, org_id=ORG_A,
        )
        self.manager._publish.assert_called_once_with(
            "user", "user1", {"type": "message_chunk"}, org_id=ORG_A,
        )

    @pytest.mark.asyncio
    async def test_task_subscribers_are_partitioned_by_org(self):
        """相同 task_id 的本地订阅按 org_id 使用复合键隔离。"""
        self.manager._task_subscribers = {
            ("task-1", ORG_A): {"conn_a"},
            ("task-1", ORG_B): {"conn_b"},
        }

        await self.manager.send_to_task_subscribers(
            "task-1", {"type": "message_chunk"}, org_id=ORG_A,
        )

        self.manager.send_to_connection.assert_called_once_with(
            "conn_a", {"type": "message_chunk"},
        )

    @pytest.mark.asyncio
    async def test_task_or_user_fallback_is_partitioned_by_org(self):
        """无本地订阅时，用户兜底也只能命中目标 org。"""
        await self.manager.send_to_task_or_user(
            "task-1", "user1", {"type": "message_chunk"}, org_id=ORG_B,
        )

        self.manager.send_to_connection.assert_called_once_with(
            "conn_b", {"type": "message_chunk"},
        )

    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe_use_composite_task_scope(self):
        """订阅生命周期始终使用连接自身的 org_id 构造复合键。"""
        from services.websocket_manager import Connection

        connection = Connection(
            websocket=MagicMock(),
            user_id="user1",
            conn_id="conn_a",
            org_id=ORG_A,
        )
        self.manager._conn_index["conn_a"] = connection

        assert await self.manager.subscribe_task("conn_a", "task-1") is True
        assert self.manager._task_subscribers == {
            ("task-1", ORG_A): {"conn_a"},
        }

        await self.manager.unsubscribe_task("conn_a", "task-1")
        assert self.manager._task_subscribers == {}
        assert connection.subscribed_tasks == set()
