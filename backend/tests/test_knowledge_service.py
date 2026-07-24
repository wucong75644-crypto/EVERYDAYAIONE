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

# ============ knowledge_config 测试 ============


class TestKnowledgeConfig:
    """knowledge_config.py 测试"""

    def test_compute_content_hash_deterministic(self):
        """相同输入产生相同哈希"""
        from services.knowledge_config import compute_content_hash

        h1 = compute_content_hash("model", "title", "content")
        h2 = compute_content_hash("model", "title", "content")
        assert h1 == h2
        assert len(h1) == 32

    def test_compute_content_hash_different_inputs(self):
        """不同输入产生不同哈希"""
        from services.knowledge_config import compute_content_hash

        h1 = compute_content_hash("model", "title1", "content")
        h2 = compute_content_hash("model", "title2", "content")
        assert h1 != h2

    def test_format_knowledge_node(self):
        """格式化知识节点"""
        from services.knowledge_config import format_knowledge_node

        row = {
            "id": "test-id",
            "category": "model",
            "subcategory": "chat",
            "title": "Test",
            "content": "Content",
            "confidence": 0.8,
            "hit_count": 3,
            "source": "auto",
            "metadata": {"key": "value"},
        }
        result = format_knowledge_node(row)
        assert result["id"] == "test-id"
        assert result["category"] == "model"
        assert result["confidence"] == 0.8

    def test_cache_operations(self):
        """搜索缓存读写"""
        from services.knowledge_config import (
            get_cached_search,
            set_cached_search,
            invalidate_search_cache,
        )

        assert get_cached_search("key") is None

        data = [{"id": "1", "title": "test"}]
        set_cached_search("key", data)
        assert get_cached_search("key") == data

        invalidate_search_cache()
        assert get_cached_search("key") is None

    @pytest.mark.asyncio
    async def test_kb_unavailable_without_db_url(self, mock_settings):
        """无 DB URL 时知识库不可用"""
        mock_settings.effective_db_url = None
        from services.knowledge_config import _get_pg_pool, is_kb_available

        import services.knowledge_config as cfg
        cfg._kb_available = None  # reset
        result = await _get_pg_pool()
        assert result is None
        assert is_kb_available() is False

    @pytest.mark.asyncio
    async def test_compute_embedding_no_api_key(self, mock_settings):
        """无 API key 时跳过 embedding"""
        mock_settings.dashscope_api_key = None
        from services.knowledge_config import compute_embedding

        result = await compute_embedding("test text")
        assert result is None


# ============ knowledge_service 测试 ============


class TestRecordMetric:
    """指标记录测试"""

    @pytest.mark.asyncio
    async def test_record_metric_skips_when_disabled(self):
        """知识库未启用时跳过"""
        with patch("services.knowledge_metrics.is_kb_available", return_value=False):
            from services.knowledge_metrics import record_metric

            # 不应抛异常
            await record_metric(
                task_type="chat", model_id="test", status="success",
            )

    @pytest.mark.asyncio
    async def test_record_metric_success(self, mock_pg_connection, mock_conn, mock_cursor):
        """成功记录指标"""
        with patch("services.knowledge_metrics.is_kb_available", return_value=True), \
             patch("services.knowledge_metrics.get_pg_connection", return_value=mock_pg_connection):
            from services.knowledge_metrics import record_metric

            await record_metric(
                task_type="chat",
                model_id="gemini-3-pro",
                status="success",
                cost_time_ms=1500,
                prompt_tokens=100,
                completion_tokens=200,
                user_id="user-123",
            )

            mock_cursor.execute.assert_called_once()
            mock_conn.commit.assert_not_called()


class TestAddKnowledge:
    """知识 CRUD 测试"""

    @pytest.mark.asyncio
    async def test_add_new_knowledge(self, mock_pg_connection, mock_conn, mock_cursor):
        """添加新知识"""
        node_id = str(uuid4())
        # fetchone 依次返回：hash 检查=None, count=0, INSERT=id
        mock_cursor.fetchone = AsyncMock(
            side_effect=[None, (0,), (node_id,)]
        )

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", return_value=mock_pg_connection), \
             patch("services.knowledge_service.compute_embedding", return_value=None), \
             patch("services.knowledge_service.invalidate_search_cache"):
            from services.knowledge_service import add_knowledge

            result = await add_knowledge(
                category="model",
                node_type="capability",
                title="Test Knowledge",
                content="Test content",
            )

            assert result == node_id

    @pytest.mark.asyncio
    async def test_add_duplicate_hash(self, mock_pg_connection, mock_conn, mock_cursor):
        """重复 hash 更新已有节点"""
        existing_id = str(uuid4())
        mock_cursor.fetchone = AsyncMock(
            return_value=(existing_id, "auto", 0.5),
        )

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", return_value=mock_pg_connection), \
             patch("services.knowledge_service.invalidate_search_cache"):
            from services.knowledge_service import add_knowledge

            result = await add_knowledge(
                category="model",
                node_type="capability",
                title="Existing",
                content="Same content",
            )

            assert result == existing_id

    @pytest.mark.asyncio
    async def test_add_skips_seed_overwrite(self, mock_pg_connection, mock_conn, mock_cursor):
        """自动提取不覆盖种子知识"""
        seed_id = str(uuid4())
        mock_cursor.fetchone = AsyncMock(
            return_value=(seed_id, "seed", 1.0),
        )

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_pg_connection", return_value=mock_pg_connection):
            from services.knowledge_service import add_knowledge

            result = await add_knowledge(
                category="model",
                node_type="capability",
                title="Seed Override Attempt",
                content="Should not overwrite",
                source="auto",
            )

            assert result == seed_id
            # 不应调用 UPDATE（种子知识保护）
            assert mock_cursor.execute.call_count == 1  # 只有查询，无更新


class TestSearchRelevant:
    """知识检索测试"""

    @pytest.mark.asyncio
    async def test_search_returns_cached(self):
        """缓存命中直接返回"""
        cached_data = [{"id": "1", "title": "cached"}]

        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_cached_search", return_value=cached_data):
            from services.knowledge_service import search_relevant

            result = await search_relevant("test query")
            assert result == cached_data

    @pytest.mark.asyncio
    async def test_search_returns_empty_when_disabled(self):
        """知识库禁用时返回空"""
        with patch("services.knowledge_service.is_kb_available", return_value=False):
            from services.knowledge_service import search_relevant

            result = await search_relevant("test query")
            assert result == []

    @pytest.mark.asyncio
    async def test_search_returns_empty_without_embedding(self):
        """embedding 失败时返回空"""
        with patch("services.knowledge_service.is_kb_available", return_value=True), \
             patch("services.knowledge_service.get_cached_search", return_value=None), \
             patch("services.knowledge_service.compute_embedding", return_value=None):
            from services.knowledge_service import search_relevant

            result = await search_relevant("test query")
            assert result == []


# ============ knowledge_extractor 测试 ============
