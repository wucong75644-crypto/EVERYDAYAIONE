"""
多租户 org_id 隔离集成测试

覆盖 Phase 9-12 的核心 org_id 传递链路：
- KuaiMaiClient token 缓存 org_id 隔离
- MemoryService _mem0_uid 转换
- IntentRouter route(org_id) → _record_routing_signal(org_id)
- IntentLearning org_id 传递
- WebSocketManager Connection org_id + broadcast 过滤
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# KuaiMaiClient token 缓存 org_id 隔离
# ============================================================


class TestKuaiMaiClientOrgId:
    """KuaiMaiClient._token_cache_key org_id 隔离"""

    def test_default_client_uses_default_key(self):
        from services.kuaimai.client import KuaiMaiClient
        client = KuaiMaiClient(app_key="k", app_secret="s", access_token="t")
        assert client._token_cache_key("token") == "kuaimai:token:default"
        assert client._token_cache_key("refresh") == "kuaimai:refresh:default"

    def test_org_client_uses_org_key(self):
        from services.kuaimai.client import KuaiMaiClient
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t", org_id="org-123"
        )
        assert client._token_cache_key("token") == "kuaimai:token:org-123"
        assert client._token_cache_key("refresh") == "kuaimai:refresh:org-123"

    def test_different_orgs_different_keys(self):
        from services.kuaimai.client import KuaiMaiClient
        c1 = KuaiMaiClient(app_key="k", app_secret="s", access_token="t", org_id="org-a")
        c2 = KuaiMaiClient(app_key="k", app_secret="s", access_token="t", org_id="org-b")
        assert c1._token_cache_key("token") != c2._token_cache_key("token")


# ============================================================
# MemoryService _mem0_uid 转换
# ============================================================


class TestMemoryServiceMemoUid:
    """MemoryService._mem0_uid org-scoped user_id 生成"""

    def test_personal_when_no_org(self):
        from services.memory_service import MemoryService
        assert MemoryService._mem0_uid("user-1") == "personal:user-1"
        assert MemoryService._mem0_uid("user-1", org_id=None) == "personal:user-1"

    def test_org_scoped_uid(self):
        from services.memory_service import MemoryService
        assert MemoryService._mem0_uid("user-1", org_id="org-x") == "org_org-x:user-1"

    def test_different_orgs_different_uids(self):
        from services.memory_service import MemoryService
        uid_a = MemoryService._mem0_uid("user-1", org_id="org-a")
        uid_b = MemoryService._mem0_uid("user-1", org_id="org-b")
        assert uid_a != uid_b

    def test_consistent_with_data_isolation(self):
        """与 core.data_isolation.get_mem0_user_id 保持一致"""
        from services.memory_service import MemoryService
        from core.data_isolation import get_mem0_user_id
        from api.deps import OrgContext

        # 散客
        ctx_personal = OrgContext(user_id="u1", org_id=None)
        assert MemoryService._mem0_uid("u1") == get_mem0_user_id(ctx_personal)

        # 企业
        ctx_org = OrgContext(user_id="u1", org_id="org-1")
        assert MemoryService._mem0_uid("u1", org_id="org-1") == get_mem0_user_id(ctx_org)


# ============================================================
# MemoryService CRUD org_id 传递
# ============================================================


class TestMemoryServiceOrgIdPassing:
    """MemoryService 各方法正确传递 org_id 到 Mem0"""

    @staticmethod
    def _inject_mem0(mock_mem0):
        """直接注入 Mem0 全局单例"""
        import services.memory_config as cfg
        cfg._mem0_instance = mock_mem0
        cfg._mem0_available = True

    @staticmethod
    def _reset_mem0():
        import services.memory_config as cfg
        cfg._mem0_instance = None
        cfg._mem0_available = None

    @pytest.mark.asyncio
    async def test_get_all_uses_mem0_uid(self):
        """get_all_memories 用 org-scoped uid 调 Mem0"""
        from services.memory_service import MemoryService

        mock_mem0 = AsyncMock()
        mock_mem0.get_all.return_value = []
        self._inject_mem0(mock_mem0)

        try:
            svc = MemoryService(MagicMock())
            await svc.get_all_memories("u1", org_id="org-x")
            mock_mem0.get_all.assert_awaited_once_with(user_id="org_org-x:u1")
        finally:
            self._reset_mem0()

    @pytest.mark.asyncio
    async def test_get_all_personal_uses_personal_uid(self):
        """散客 get_all_memories 用 personal:uid"""
        from services.memory_service import MemoryService

        mock_mem0 = AsyncMock()
        mock_mem0.get_all.return_value = []
        self._inject_mem0(mock_mem0)

        try:
            svc = MemoryService(MagicMock())
            await svc.get_all_memories("u1")
            mock_mem0.get_all.assert_awaited_once_with(user_id="personal:u1")
        finally:
            self._reset_mem0()

    @pytest.mark.asyncio
    async def test_delete_all_uses_mem0_uid(self):
        """delete_all_memories 用 org-scoped uid"""
        from services.memory_service import MemoryService

        mock_mem0 = AsyncMock()
        self._inject_mem0(mock_mem0)

        try:
            svc = MemoryService(MagicMock())
            await svc.delete_all_memories("u1", org_id="org-y")
            mock_mem0.delete_all.assert_awaited_once_with(user_id="org_org-y:u1")
        finally:
            self._reset_mem0()


# ============================================================
# IntentRouter org_id 传递
# ============================================================


class TestIntentRouterOrgId:
    """IntentRouter.route 正确传递 org_id"""

    @pytest.mark.asyncio
    async def test_enhance_with_knowledge_passes_org_id(self):
        """_enhance_with_knowledge(org_id) → search_relevant(org_id)"""
        from services.intent_router import IntentRouter

        router = IntentRouter()

        with patch("services.knowledge_service.search_relevant", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            await router._enhance_with_knowledge("测试", org_id="org-z")

            mock_search.assert_awaited_once()
            call_kwargs = mock_search.call_args[1]
            assert call_kwargs.get("org_id") == "org-z"

        await router.close()

    @pytest.mark.asyncio
    async def test_record_routing_signal_includes_org_id(self):
        """_record_routing_signal 传 org_id 到 record_metric"""
        from services.intent_router import IntentRouter, RoutingDecision
        from schemas.message import GenerationType

        decision = RoutingDecision(
            generation_type=GenerationType.CHAT,
            routed_by="test",
        )

        with patch("services.knowledge_service.record_metric", new_callable=AsyncMock) as mock_metric:
            IntentRouter._record_routing_signal(
                decision, "u1", 10, False, "test_model", org_id="org-1"
            )
            await asyncio.sleep(0.05)

            mock_metric.assert_called_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["org_id"] == "org-1"
            assert call_kwargs["user_id"] == "u1"


# ============================================================
# IntentLearning org_id 传递
# ============================================================


class TestIntentLearningOrgId:
    """intent_learning 函数签名 org_id 传递"""

    @pytest.mark.asyncio
    async def test_record_ask_user_context_passes_org_id(self):
        """record_ask_user_context 传 org_id 到 record_metric"""
        with patch("services.intent_learning.record_metric", new_callable=AsyncMock) as mock_metric:
            from services.intent_learning import record_ask_user_context
            await record_ask_user_context(
                conversation_id="c1",
                user_id="u1",
                original_message="测试消息",
                ask_options="选项A|选项B",
                org_id="org-abc",
            )

            mock_metric.assert_awaited_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["org_id"] == "org-abc"

    @pytest.mark.asyncio
    async def test_write_intent_pattern_passes_org_id(self):
        """_write_intent_pattern 传 org_id 到 add_knowledge"""
        with patch("services.knowledge_service.add_knowledge", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = "node-1"
            from services.intent_learning import _write_intent_pattern
            await _write_intent_pattern(
                original_expression="帮我画一张图",
                confirmed_tool="route_to_image",
                user_response="图片生成",
                ask_options="选项",
                org_id="org-xyz",
            )

            mock_add.assert_awaited_once()
            call_kwargs = mock_add.call_args[1]
            assert call_kwargs["org_id"] == "org-xyz"


# ============================================================
# WebSocketManager Connection org_id
# ============================================================


class TestWebSocketManagerOrgId:
    """WebSocket Connection org_id 字段 + broadcast 过滤"""

    @pytest.mark.asyncio
    async def test_connection_stores_org_id(self):
        """connect 时 org_id 存入 Connection"""
        from services.websocket_manager import WebSocketManager, Connection

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()

        conn_id = await mgr.connect(ws, "u1", org_id="org-1")

        conn = mgr._conn_index.get(conn_id)
        assert conn is not None
        assert conn.org_id == "org-1"
        assert conn.user_id == "u1"

        await mgr.disconnect(conn_id)

    @pytest.mark.asyncio
    async def test_connection_org_id_none_default(self):
        """不传 org_id 时默认 None"""
        from services.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()

        conn_id = await mgr.connect(ws, "u1")

        conn = mgr._conn_index.get(conn_id)
        assert conn.org_id is None

        await mgr.disconnect(conn_id)

    @pytest.mark.asyncio
    async def test_broadcast_filters_by_org_id(self):
        """broadcast_all(org_id=X) 只发给该 org 的连接"""
        from services.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        # 创建两个不同 org 的连接
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        conn1 = await mgr.connect(ws1, "u1", org_id="org-a")

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        conn2 = await mgr.connect(ws2, "u2", org_id="org-b")

        # 广播给 org-a
        await mgr.broadcast_all({"type": "test"}, org_id="org-a")

        ws1.send_json.assert_called_once_with({"type": "test"})
        ws2.send_json.assert_not_called()

        await mgr.disconnect(conn1)
        await mgr.disconnect(conn2)

    @pytest.mark.asyncio
    async def test_broadcast_no_org_sends_to_all(self):
        """broadcast_all(org_id=None) 发给所有连接"""
        from services.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        conn1 = await mgr.connect(ws1, "u1", org_id="org-a")

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        conn2 = await mgr.connect(ws2, "u2", org_id="org-b")

        await mgr.broadcast_all({"type": "all"})

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()

        await mgr.disconnect(conn1)
        await mgr.disconnect(conn2)
