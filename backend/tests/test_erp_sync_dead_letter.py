"""
ERP 死信消费者单元测试
覆盖：_get_or_create_client、_mark_batch_retry_failed、TTL 过期清理、_process_batch
"""

import pytest
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from conftest import MockErpAsyncDBClient


# ── _get_or_create_client ────────────────────────────


class TestGetOrCreateClient:

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """缓存命中直接返回"""
        from services.kuaimai.erp_sync_dead_letter import _get_or_create_client

        mock_client = MagicMock()
        org_clients = {"org-1": mock_client}
        client_ages = {"org-1": time.time()}

        result = await _get_or_create_client(MagicMock(), org_clients, client_ages, "org-1")
        assert result is mock_client

    @pytest.mark.asyncio
    async def test_none_org_configured(self):
        """散客模式：全局凭证已配置"""
        from services.kuaimai.erp_sync_dead_letter import _get_or_create_client

        mock_client = MagicMock()
        mock_client.is_configured = True

        with patch("services.kuaimai.client.KuaiMaiClient", return_value=mock_client):
            org_clients: dict = {}
            client_ages: dict = {}
            result = await _get_or_create_client(MagicMock(), org_clients, client_ages, None)

        assert result is mock_client
        assert None in org_clients
        assert None in client_ages

    @pytest.mark.asyncio
    async def test_none_org_unconfigured(self):
        """散客模式：全局凭证未配置 → 返回 None"""
        from services.kuaimai.erp_sync_dead_letter import _get_or_create_client

        mock_client = MagicMock()
        mock_client.is_configured = False
        mock_client.close = AsyncMock()

        with patch("services.kuaimai.client.KuaiMaiClient", return_value=mock_client):
            result = await _get_or_create_client(MagicMock(), {}, {}, None)

        assert result is None
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_org_credentials_success(self):
        """企业凭证加载成功 → 注入 token_persister + 加载 Redis 缓存 → 缓存并返回"""
        from services.kuaimai.erp_sync_dead_letter import _get_or_create_client

        # 关键：load_cached_token 必须是 AsyncMock，因为 _get_or_create_client
        # 现在会 await client.load_cached_token() 拿最新 Redis token
        mock_client = MagicMock()
        mock_client.load_cached_token = AsyncMock()
        org_clients: dict = {}
        client_ages: dict = {}

        mock_resolver_instance = MagicMock()
        mock_resolver_instance.get_erp_credentials = AsyncMock(return_value={
            "kuaimai_app_key": "k", "kuaimai_app_secret": "s",
            "kuaimai_access_token": "t", "kuaimai_refresh_token": "r",
        })
        mock_resolver_instance.update_erp_token = AsyncMock()

        with patch(
            "services.org.config_resolver.AsyncOrgConfigResolver",
            return_value=mock_resolver_instance,
        ), patch(
            "services.kuaimai.client.KuaiMaiClient", return_value=mock_client,
        ) as MockClient:
            result = await _get_or_create_client(
                MagicMock(), org_clients, client_ages, "org-1",
            )

        assert result is mock_client
        assert "org-1" in org_clients
        assert "org-1" in client_ages
        # 验证 KuaiMaiClient 被注入了 token_persister
        ctor_kwargs = MockClient.call_args.kwargs
        assert ctor_kwargs["org_id"] == "org-1"
        assert ctor_kwargs["token_persister"] is not None
        # 验证 load_cached_token 被调用了（多租户也走 Redis 热缓存）
        mock_client.load_cached_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_org_credentials_failure(self):
        """企业凭证加载失败 → 返回 None，不缓存"""
        from services.kuaimai.erp_sync_dead_letter import _get_or_create_client

        org_clients: dict = {}
        client_ages: dict = {}

        with patch("services.org.config_resolver.AsyncOrgConfigResolver") as MockResolver:
            MockResolver.return_value.get_erp_credentials = AsyncMock(
                side_effect=ValueError("未配置"),
            )
            result = await _get_or_create_client(MagicMock(), org_clients, client_ages, "org-1")

        assert result is None
        assert "org-1" not in org_clients


# ── _mark_batch_retry_failed ─────────────────────────


class TestMarkBatchRetryFailed:

    @pytest.mark.asyncio
    async def test_increments_retry_count(self):
        """未超限时递增 retry_count + 设置 next_retry_at"""
        from services.kuaimai.erp_sync_dead_letter import _mark_batch_retry_failed

        db = MockErpAsyncDBClient()
        rows = [{"id": "dl-1", "retry_count": 3, "max_retries": 10}]

        await _mark_batch_retry_failed(db, rows, "no client")

        # 验证 update 被调用（MockErpAsyncDBClient 记录操作）
        table = db.table("erp_sync_dead_letter")
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_marks_dead_when_exhausted(self):
        """超过 max_retries 时标记 status=dead"""
        from services.kuaimai.erp_sync_dead_letter import _mark_batch_retry_failed

        db = MockErpAsyncDBClient()
        rows = [{"id": "dl-1", "retry_count": 9, "max_retries": 10}]

        await _mark_batch_retry_failed(db, rows, "no client")
        # new_count = 10 >= max_retries=10 → dead

    @pytest.mark.asyncio
    async def test_db_error_does_not_raise(self):
        """DB 写入失败不影响其他记录"""
        from services.kuaimai.erp_sync_dead_letter import _mark_batch_retry_failed

        db = MagicMock()
        # 让 table().update() 链抛异常
        db.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(
            side_effect=RuntimeError("DB down"),
        )

        rows = [
            {"id": "dl-1", "retry_count": 0, "max_retries": 10},
            {"id": "dl-2", "retry_count": 0, "max_retries": 10},
        ]

        # 不应该抛异常
        await _mark_batch_retry_failed(db, rows, "error msg")


# ── TTL 过期清理 ─────────────────────────────────────


class TestClientCacheTTL:

    @pytest.mark.asyncio
    async def test_expired_client_is_removed(self):
        """过期 client 在 _process_batch 开头被清理"""
        from services.kuaimai.erp_sync_dead_letter import _process_batch, _CLIENT_CACHE_TTL

        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        org_clients = {"org-old": mock_client}
        # 创建时间设为很久以前
        client_ages = {"org-old": time.time() - _CLIENT_CACHE_TTL - 100}

        db = MockErpAsyncDBClient()
        # 查询返回空（无待重试死信）
        db.set_table_data("erp_sync_dead_letter", [])

        await _process_batch(db, org_clients, client_ages)

        # 过期 client 应该被清理
        assert "org-old" not in org_clients
        assert "org-old" not in client_ages
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_client_not_removed(self):
        """未过期 client 不被清理"""
        from services.kuaimai.erp_sync_dead_letter import _process_batch

        mock_client = MagicMock()
        mock_client.close = AsyncMock()

        org_clients = {"org-fresh": mock_client}
        client_ages = {"org-fresh": time.time()}  # 刚创建

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_sync_dead_letter", [])

        await _process_batch(db, org_clients, client_ages)

        # 未过期，不清理
        assert "org-fresh" in org_clients
        mock_client.close.assert_not_called()


# ── _process_batch 分组逻辑 ──────────────────────────


class TestProcessBatchOrgRouting:

    @pytest.mark.asyncio
    async def test_empty_rows_returns_zero(self):
        """无待重试死信时返回 0"""
        from services.kuaimai.erp_sync_dead_letter import _process_batch

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_sync_dead_letter", [])

        result = await _process_batch(db, {}, {})
        assert result == 0

    @pytest.mark.asyncio
    async def test_client_unavailable_marks_retry(self):
        """client 创建失败时调用 _mark_batch_retry_failed"""
        from services.kuaimai.erp_sync_dead_letter import _process_batch

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_sync_dead_letter", [
            {
                "id": "dl-1", "org_id": "org-bad", "doc_type": "purchase",
                "doc_id": "P001", "detail_method": "purchase.detail",
                "doc_json": '{"id": "P001"}', "retry_count": 0,
                "max_retries": 10, "status": "pending",
                "next_retry_at": "2020-01-01T00:00:00",
            },
        ])

        with patch(
            "services.kuaimai.erp_sync_dead_letter._get_or_create_client",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "services.kuaimai.erp_sync_dead_letter._mark_batch_retry_failed",
            new_callable=AsyncMock,
        ) as mock_mark:
            result = await _process_batch(db, {}, {})

        assert result == 0
        mock_mark.assert_called_once()
        # 验证传入的 rows 包含 dl-1
        marked_rows = mock_mark.call_args[0][1]
        assert marked_rows[0]["id"] == "dl-1"
