"""
知识库服务单元测试

覆盖指标记录、知识 CRUD、去重、检索、种子导入、图服务、提取器。
"""

import json
import sys
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))



from testing.knowledge_test_support import (
    mock_conn, mock_cursor, mock_pg_connection, mock_settings,
    reset_kb_globals,
)

class TestMaxPerCategoryEviction:
    """知识库 per-category 淘汰机制"""

    def _setup_mocks(self, fetchone_side_effect):
        """构造完整的 pg mock 链"""
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=fetchone_side_effect)

        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=cursor),
            __aexit__=AsyncMock(return_value=False),
        ))
        conn.commit = AsyncMock()

        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)

        return cursor, conn_ctx

    @pytest.mark.asyncio
    async def test_eviction_triggered_when_over_limit(self):
        """超过 max_per_category 时触发淘汰"""
        cursor, conn_ctx = self._setup_mocks([
            None, (600,), (501,), None, ("new-id",),
        ])
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test content",
                max_per_category=500,
            )
            sqls = [str(c) for c in cursor.execute.call_args_list]
            eviction = [s for s in sqls if "is_deleted = TRUE" in s and "category" in s]
            assert len(eviction) == 1

    @pytest.mark.asyncio
    async def test_no_eviction_when_under_limit(self):
        """未超过 max_per_category 时不淘汰"""
        cursor, conn_ctx = self._setup_mocks([
            None, (100,), (50,), ("new-id",),
        ])
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test content",
                max_per_category=500,
            )
            sqls = [str(c) for c in cursor.execute.call_args_list]
            eviction = [s for s in sqls if "is_deleted = TRUE" in s and "category" in s]
            assert len(eviction) == 0

    @pytest.mark.asyncio
    async def test_no_eviction_when_max_per_category_none(self):
        """max_per_category=None 时跳过 per-category 检查"""
        cursor, conn_ctx = self._setup_mocks([
            None, (100,), ("new-id",),
        ])
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test content",
            )
            sqls = [str(c) for c in cursor.execute.call_args_list]
            cat_count = [s for s in sqls if "COUNT" in s and "category" in s]
            assert len(cat_count) == 0


class TestSchemaValidation:
    """方案 C：add_knowledge 入口 schema 校验测试"""

    @pytest.mark.asyncio
    async def test_rejects_invalid_category(self):
        """非白名单 category 触发 ValueError 且不调用 PG"""
        from services.knowledge_service import add_knowledge

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock) as mock_pg:
            with pytest.raises(ValueError, match="invalid category"):
                await add_knowledge(
                    category="routing",  # 旧的非法值
                    node_type="routing_pattern",
                    title="test", content="test content",
                )
            # 校验失败时 PG 连接不应被打开（最早期拦截）
            mock_pg.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_invalid_node_type(self):
        """非白名单 node_type 触发 ValueError 且不调用 PG"""
        from services.knowledge_service import add_knowledge

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock) as mock_pg:
            with pytest.raises(ValueError, match="invalid node_type"):
                await add_knowledge(
                    category="experience",
                    node_type="experience",  # 旧的非法值（schema 没声明，但应用层禁用）
                    title="test", content="test content",
                )
            mock_pg.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_invalid_source(self):
        """非白名单 source 触发 ValueError 且不调用 PG

        防"修一道露一道"：方案 C 修了 category/node_type 后，
        source 是另一道 PG CHECK 闸门（'auto'/'seed'/'manual'/'aggregated'）。
        旧 ERPAgent 用 source='erp_agent' 在生产触发了 source_check 失败，
        本测试确保未来类似越界值在应用层被立即拦截。
        """
        from services.knowledge_service import add_knowledge

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock) as mock_pg:
            with pytest.raises(ValueError, match="invalid source"):
                await add_knowledge(
                    category="experience",
                    node_type="routing_pattern",
                    title="test", content="test content",
                    source="erp_agent",  # 旧的非法值（PG CHECK 不允许）
                )
            mock_pg.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_routing_pattern(self):
        """方案 C 新增的 routing_pattern node_type 必须接受"""
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=[None, (0,), ("new-id",)])
        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=cursor),
            __aexit__=AsyncMock(return_value=False),
        ))
        conn.commit = AsyncMock()
        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            result = await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test",
            )
            assert result == "new-id"

    @pytest.mark.asyncio
    async def test_accepts_failure_pattern(self):
        """方案 C 新增的 failure_pattern node_type 必须接受"""
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=[None, (0,), ("new-id",)])
        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=cursor),
            __aexit__=AsyncMock(return_value=False),
        ))
        conn.commit = AsyncMock()
        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            result = await add_knowledge(
                category="experience", node_type="failure_pattern",
                title="test", content="test",
            )
            assert result == "new-id"


class TestMaxPerNodeTypeEviction:
    """方案 C：per-node_type 淘汰机制测试（与 per-category 平行独立）"""

    def _setup_mocks(self, fetchone_side_effect):
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=fetchone_side_effect)
        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=cursor),
            __aexit__=AsyncMock(return_value=False),
        ))
        conn.commit = AsyncMock()
        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)
        return cursor, conn_ctx

    @pytest.mark.asyncio
    async def test_node_type_eviction_triggered_when_over_limit(self):
        """超过 max_per_node_type 时按 node_type 维度淘汰"""
        # 流程: dedup_hash → evict_global COUNT → evict_node_type COUNT → INSERT
        cursor, conn_ctx = self._setup_mocks([
            None, (100,), (401,), ("new-id",),
        ])
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test",
                max_per_node_type=400,
            )
            sqls = [str(c) for c in cursor.execute.call_args_list]
            eviction = [s for s in sqls if "is_deleted = TRUE" in s and "node_type" in s]
            assert len(eviction) == 1

    @pytest.mark.asyncio
    async def test_node_type_eviction_independent_from_category(self):
        """per-node_type 和 per-category 互相独立，可共存"""
        # 流程: dedup → evict_global → evict_category COUNT → evict_node_type COUNT → INSERT
        cursor, conn_ctx = self._setup_mocks([
            None, (100,), (10,), (10,), ("new-id",),
        ])
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=None):
            from services.knowledge_service import add_knowledge
            await add_knowledge(
                category="experience", node_type="routing_pattern",
                title="test", content="test",
                max_per_category=500, max_per_node_type=400,
            )
            sqls = [str(c) for c in cursor.execute.call_args_list]
            cat_count = [s for s in sqls if "COUNT" in s and "category" in s and "node_type" not in s]
            nt_count = [s for s in sqls if "COUNT" in s and "node_type" in s]
            assert len(cat_count) == 1
            assert len(nt_count) == 1


class TestSearchRelevantWeighted:
    """方案 C：search_relevant 加权排序 + min_confidence 过滤测试"""

    def _setup_search_mocks(self):
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        cursor.description = []
        cursor.fetchone = AsyncMock(return_value=None)
        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=cursor),
            __aexit__=AsyncMock(return_value=False),
        ))
        conn.commit = AsyncMock()
        conn_ctx = AsyncMock()
        conn_ctx.__aenter__ = AsyncMock(return_value=conn)
        conn_ctx.__aexit__ = AsyncMock(return_value=False)
        return cursor, conn_ctx

    @pytest.mark.asyncio
    async def test_min_confidence_filter_applied(self):
        """传 min_confidence 时 SQL WHERE 包含 confidence >= 阈值"""
        cursor, conn_ctx = self._setup_search_mocks()
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=[0.1] * 1024):
            from services.knowledge_service import search_relevant
            await search_relevant("test query", min_confidence=0.6)
            sql = str(cursor.execute.call_args_list[0])
            assert "confidence >= %(min_conf)s" in sql

    @pytest.mark.asyncio
    async def test_min_confidence_omitted_by_default(self):
        """未传 min_confidence 时 SQL WHERE 不包含 confidence 过滤"""
        cursor, conn_ctx = self._setup_search_mocks()
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=[0.1] * 1024):
            from services.knowledge_service import search_relevant
            await search_relevant("test query")
            sql = str(cursor.execute.call_args_list[0])
            assert "confidence >= " not in sql

    @pytest.mark.asyncio
    async def test_weighted_ordering_in_sql(self):
        """ORDER BY 公式包含 similarity*0.7 + confidence*0.3"""
        cursor, conn_ctx = self._setup_search_mocks()
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=[0.1] * 1024):
            from services.knowledge_service import search_relevant
            await search_relevant("test query")
            sql = str(cursor.execute.call_args_list[0])
            assert "* 0.7" in sql and "* 0.3" in sql
            assert "ORDER BY" in sql

    @pytest.mark.asyncio
    async def test_node_type_filter_applied(self):
        """传 node_type 时 SQL WHERE 包含 node_type 过滤"""
        cursor, conn_ctx = self._setup_search_mocks()
        with patch("services.knowledge_service.get_pg_connection", new_callable=AsyncMock, return_value=conn_ctx), \
             patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.compute_embedding", new_callable=AsyncMock, return_value=[0.1] * 1024):
            from services.knowledge_service import search_relevant
            await search_relevant("test query", node_type="routing_pattern")
            sql = str(cursor.execute.call_args_list[0])
            assert "node_type = %(node_type)s" in sql
