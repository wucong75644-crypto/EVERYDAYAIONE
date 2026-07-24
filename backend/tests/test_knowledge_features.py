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
            assert 0.9 <= item["confidence"] <= 1.0
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


# ============ Per-Category Eviction ============
