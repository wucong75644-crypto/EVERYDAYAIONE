"""
多租户 org_id 隔离集成测试

覆盖 Phase 9-12 的核心 org_id 传递链路：
- KuaiMaiClient token 缓存 org_id 隔离
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
# 注：intent_learning 模块已于 2026-04-11 删除（见 docs/document/DEPRECATED_intent_learning.md）。
# 原 TestIntentLearningOrgId 类的 2 个测试随之移除。


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
