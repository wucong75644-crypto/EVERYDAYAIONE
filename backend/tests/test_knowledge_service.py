"""
知识库服务单元测试

覆盖指标记录、知识 CRUD、去重、检索、种子导入、图服务、提取器。
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ============ Fixtures ============


@pytest.fixture(autouse=True)
def reset_kb_globals():
    """每个测试前重置知识库全局状态"""
    import services.knowledge_config as cfg

    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()
    yield
    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()


@pytest.fixture
def mock_settings():
    """Mock 配置（知识库开启）"""
    with patch("services.knowledge_config.settings") as mock_s:
        mock_s.kb_enabled = True
        mock_s.kb_extraction_model = "qwen-turbo"
        mock_s.kb_extraction_fallback_model = "qwen-plus"
        mock_s.kb_extraction_timeout = 3.0
        mock_s.kb_search_limit = 5
        mock_s.kb_search_threshold = 0.5
        mock_s.kb_max_nodes = 5000
        mock_s.kb_cache_ttl = 600
        mock_s.kb_confidence_boost = 0.1
        mock_s.kb_confidence_decay_days = 30
        mock_s.database_url = "postgresql://test"
        mock_s.dashscope_api_key = "test-key"
        mock_s.dashscope_base_url = "https://test.api.com"
        yield mock_s


@pytest.fixture
def mock_cursor():
    """Mock psycopg 异步游标"""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.description = []
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    """Mock psycopg 异步连接（context manager）"""
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_cursor),
        __aexit__=AsyncMock(return_value=False),
    ))
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_pg_connection(mock_conn):
    """Mock get_pg_connection 返回 context manager"""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


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
        with patch("services.knowledge_service.is_kb_available", return_value=False):
            from services.knowledge_service import record_metric

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
            mock_conn.commit.assert_called_once()


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


class TestKnowledgeExtractor:
    """知识提取器测试"""

    def test_parse_extraction_valid_json(self):
        """解析有效 JSON"""
        from services.knowledge_extractor import _parse_extraction

        text = '[{"category": "model", "title": "test", "content": "desc"}]'
        result = _parse_extraction(text)
        assert len(result) == 1
        assert result[0]["category"] == "model"

    def test_parse_extraction_markdown_wrapped(self):
        """解析 markdown 包裹的 JSON"""
        from services.knowledge_extractor import _parse_extraction

        text = '```json\n[{"category": "model", "title": "t", "content": "c"}]\n```'
        result = _parse_extraction(text)
        assert len(result) == 1

    def test_parse_extraction_empty_array(self):
        """空数组"""
        from services.knowledge_extractor import _parse_extraction

        result = _parse_extraction("[]")
        assert result == []

    def test_parse_extraction_invalid(self):
        """无效 JSON 返回空列表"""
        from services.knowledge_extractor import _parse_extraction

        result = _parse_extraction("not json at all")
        assert result == []

    def test_parse_extraction_extracts_from_text(self):
        """从混合文本中提取 JSON 数组"""
        from services.knowledge_extractor import _parse_extraction

        text = 'Here are the results:\n[{"category": "model", "title": "t", "content": "c"}]\nDone.'
        result = _parse_extraction(text)
        assert len(result) == 1

    def test_build_prompt(self):
        """构建提取 prompt"""
        from services.knowledge_extractor import _build_prompt

        prompt = _build_prompt(
            task_type="chat",
            model_id="gemini-3-pro",
            status="failed",
            error_message="timeout",
            retry_info="从 flash 切换到 pro",
        )
        assert "chat" in prompt
        assert "gemini-3-pro" in prompt
        assert "timeout" in prompt

    def test_infer_node_type(self):
        """推断 node_type"""
        from services.knowledge_extractor import _infer_node_type

        assert _infer_node_type({"category": "model"}) == "capability"
        assert _infer_node_type({"category": "tool"}) == "parameter"
        assert _infer_node_type({"category": "experience"}) == "pattern"

    @pytest.mark.asyncio
    async def test_extract_and_save_disabled(self):
        """知识库禁用时返回 0"""
        with patch("services.knowledge_extractor.settings") as mock_s:
            mock_s.kb_enabled = False
            from services.knowledge_extractor import extract_and_save

            result = await extract_and_save(
                task_type="chat", model_id="test", status="failed",
            )
            assert result == 0


# ============ graph_service 测试 ============


class TestGraphService:
    """图服务测试"""

    @pytest.mark.asyncio
    async def test_find_related_returns_empty_when_unavailable(self):
        """连接不可用时返回空"""
        with patch("services.graph_service.get_pg_connection", return_value=None):
            from services.graph_service import graph_service

            result = await graph_service.find_related("node-123")
            assert result == []

    @pytest.mark.asyncio
    async def test_find_path_returns_empty_when_unavailable(self):
        """连接不可用时返回空"""
        with patch("services.graph_service.get_pg_connection", return_value=None):
            from services.graph_service import graph_service

            result = await graph_service.find_path("a", "b")
            assert result == []

    @pytest.mark.asyncio
    async def test_add_edge_returns_none_when_unavailable(self):
        """连接不可用时返回 None"""
        with patch("services.graph_service.get_pg_connection", return_value=None):
            from services.graph_service import graph_service

            result = await graph_service.add_edge(
                source_id="a", target_id="b", relation_type="related_to",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_get_subgraph_returns_empty_when_unavailable(self):
        """连接不可用时返回空子图"""
        with patch("services.graph_service.get_pg_connection", return_value=None):
            from services.graph_service import graph_service

            result = await graph_service.get_subgraph(["a", "b"])
            assert result == {"nodes": [], "edges": []}


# ============ seed_knowledge 测试 ============


class TestSeedKnowledge:
    """种子知识测试"""

    def test_seed_file_is_valid_json(self):
        """种子文件是有效 JSON"""
        import os
        seed_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "seed_knowledge.json"
        )
        with open(seed_path, encoding="utf-8") as f:
            seeds = json.load(f)

        assert isinstance(seeds, list)
        assert len(seeds) > 0

    def test_seed_entries_have_required_fields(self):
        """种子条目有必须字段"""
        import os
        seed_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "seed_knowledge.json"
        )
        with open(seed_path, encoding="utf-8") as f:
            seeds = json.load(f)

        required = {"category", "title", "content", "source", "confidence"}
        for item in seeds:
            for field in required:
                assert field in item, f"Missing field '{field}' in: {item['title']}"
            assert item["source"] == "seed"
            assert item["confidence"] == 1.0
            assert item["category"] in ("model", "tool", "experience")

    def test_seed_titles_unique(self):
        """种子知识标题唯一"""
        import os
        seed_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "seed_knowledge.json"
        )
        with open(seed_path, encoding="utf-8") as f:
            seeds = json.load(f)

        titles = [s["title"] for s in seeds]
        assert len(titles) == len(set(titles)), "Duplicate seed titles found"

    @pytest.mark.asyncio
    async def test_load_seed_file_not_found(self):
        """种子文件不存在时返回 0"""
        with patch("services.knowledge_service.is_kb_available", return_value=True):
            from services.knowledge_service import load_seed_knowledge

            result = await load_seed_knowledge("/nonexistent/path.json")
            assert result == 0
