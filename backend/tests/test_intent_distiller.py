"""
意图提炼服务单元测试

覆盖：distill_intent_patterns、_aggregate_user_patterns、_distill_for_tool、
      _parse_distill_response、_write_distilled_rule
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.intent_distiller import (
    _aggregate_user_patterns,
    _call_distill_model,
    _parse_distill_response,
    _write_distilled_rule,
    distill_intent_patterns,
)


# ============================================================
# TestParseDistillResponse
# ============================================================


class TestParseDistillResponse:

    def test_valid_json(self):
        """正常 JSON 响应→解析成功"""
        text = '{"rule": "修改类动词+图片", "keywords": ["修正", "矫正"], "confidence": 0.9}'
        result = _parse_distill_response(text)
        assert result is not None
        assert result["rule"] == "修改类动词+图片"
        assert len(result["keywords"]) == 2

    def test_json_in_code_block(self):
        """Markdown code block 包裹→正常解析"""
        text = '```json\n{"rule": "test", "keywords": ["a"]}\n```'
        result = _parse_distill_response(text)
        assert result is not None
        assert result["rule"] == "test"

    def test_invalid_json(self):
        """非 JSON 文本→返回 None"""
        result = _parse_distill_response("这不是JSON")
        assert result is None

    def test_missing_rule_field(self):
        """缺少 rule 字段→返回 None"""
        text = '{"keywords": ["a", "b"]}'
        result = _parse_distill_response(text)
        assert result is None

    def test_missing_keywords_field(self):
        """缺少 keywords 字段→返回 None"""
        text = '{"rule": "test"}'
        result = _parse_distill_response(text)
        assert result is None

    def test_empty_string(self):
        """空字符串→返回 None"""
        result = _parse_distill_response("")
        assert result is None


# ============================================================
# TestAggregateUserPatterns
# ============================================================


class TestAggregateUserPatterns:

    @pytest.mark.asyncio
    @patch("services.intent_distiller.get_pg_connection")
    async def test_no_connection(self, mock_conn):
        """数据库不可用→返回空列表"""
        mock_conn.return_value = None
        result = await _aggregate_user_patterns()
        assert result == []

    @pytest.mark.asyncio
    @patch("services.intent_distiller.get_pg_connection")
    async def test_returns_rows(self, mock_get_conn):
        """正常查询→返回格式化行"""
        from types import SimpleNamespace

        mock_cur = AsyncMock()
        mock_cur.fetchall.return_value = [
            ("route_to_image", "修正图片→编辑", "content", {"key": "val"}),
        ]
        mock_cur.description = [
            SimpleNamespace(name="subcategory"),
            SimpleNamespace(name="title"),
            SimpleNamespace(name="content"),
            SimpleNamespace(name="metadata"),
        ]

        # cursor() 是同步调用，返回异步上下文管理器
        cursor_ctx = MagicMock()
        cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
        cursor_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=cursor_ctx)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        mock_get_conn.return_value = mock_ctx

        result = await _aggregate_user_patterns()
        assert len(result) == 1
        assert result[0]["subcategory"] == "route_to_image"

    @pytest.mark.asyncio
    @patch("services.intent_distiller.get_pg_connection")
    async def test_db_error_returns_empty(self, mock_conn):
        """数据库异常→返回空列表"""
        mock_conn.side_effect = Exception("DB error")
        result = await _aggregate_user_patterns()
        assert result == []


# ============================================================
# TestWriteDistilledRule
# ============================================================


class TestWriteDistilledRule:

    @pytest.mark.asyncio
    @patch("services.intent_distiller.add_knowledge")
    async def test_writes_correct_structure(self, mock_add):
        """写入正确的知识节点结构"""
        mock_add.return_value = "node-999"
        result = await _write_distilled_rule(
            "route_to_image",
            {"rule": "修改+图片→编辑", "keywords": ["修正"], "confidence": 0.85},
            sample_count=25,
        )
        assert result == "node-999"
        kw = mock_add.call_args[1]
        assert kw["category"] == "experience"
        assert kw["subcategory"] == "route_to_image"
        assert kw["node_type"] == "distilled_rule"
        assert kw["source"] == "distilled"
        assert kw["scope"] == "global"
        assert kw["confidence"] == 0.9  # 0.85 + 0.05 (sample>=20)

    @pytest.mark.asyncio
    @patch("services.intent_distiller.add_knowledge")
    async def test_confidence_capped(self, mock_add):
        """confidence 上限 0.95"""
        mock_add.return_value = "node-1"
        await _write_distilled_rule(
            "route_to_chat",
            {"rule": "test", "keywords": [], "confidence": 0.95},
            sample_count=50,
        )
        assert mock_add.call_args[1]["confidence"] == 0.95

    @pytest.mark.asyncio
    @patch("services.intent_distiller.add_knowledge")
    async def test_small_sample_no_boost(self, mock_add):
        """样本量 < 20 不加 boost"""
        mock_add.return_value = "node-2"
        await _write_distilled_rule(
            "route_to_video",
            {"rule": "test", "keywords": [], "confidence": 0.8},
            sample_count=10,
        )
        assert mock_add.call_args[1]["confidence"] == 0.8


# ============================================================
# TestDistillIntentPatterns — 主入口
# ============================================================


class TestDistillIntentPatterns:

    @pytest.mark.asyncio
    @patch("services.intent_distiller.is_kb_available")
    async def test_kb_unavailable_skips(self, mock_avail):
        """知识库不可用→直接跳过"""
        mock_avail.return_value = False
        # 不应抛异常
        await distill_intent_patterns()

    @pytest.mark.asyncio
    @patch("services.intent_distiller._aggregate_user_patterns")
    @patch("services.intent_distiller.is_kb_available")
    async def test_no_patterns_skips(self, mock_avail, mock_agg):
        """无模式数据→直接跳过"""
        mock_avail.return_value = True
        mock_agg.return_value = []
        await distill_intent_patterns()

    @pytest.mark.asyncio
    @patch("services.intent_distiller._write_distilled_rule")
    @patch("services.intent_distiller._distill_for_tool")
    @patch("services.intent_distiller._aggregate_user_patterns")
    @patch("services.intent_distiller.is_kb_available")
    async def test_below_threshold_skips(
        self, mock_avail, mock_agg, mock_distill, mock_write,
    ):
        """每组不足 5 条→不触发提炼"""
        mock_avail.return_value = True
        mock_agg.return_value = [
            {"subcategory": "route_to_image", "title": "t", "content": "c",
             "metadata": {}},
        ] * 3  # 只有 3 条
        await distill_intent_patterns()
        mock_distill.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.intent_distiller._write_distilled_rule")
    @patch("services.intent_distiller._distill_for_tool")
    @patch("services.intent_distiller._aggregate_user_patterns")
    @patch("services.intent_distiller.is_kb_available")
    async def test_above_threshold_distills(
        self, mock_avail, mock_agg, mock_distill, mock_write,
    ):
        """≥5 条模式→触发提炼"""
        mock_avail.return_value = True
        mock_agg.return_value = [
            {"subcategory": "route_to_image", "title": f"t{i}",
             "content": f"c{i}", "metadata": {}}
            for i in range(6)
        ]
        mock_distill.return_value = {
            "rule": "test rule", "keywords": ["a"], "confidence": 0.85,
        }
        mock_write.return_value = "node-1"

        await distill_intent_patterns()
        mock_distill.assert_called_once()
        mock_write.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.intent_distiller._write_distilled_rule")
    @patch("services.intent_distiller._distill_for_tool")
    @patch("services.intent_distiller._aggregate_user_patterns")
    @patch("services.intent_distiller.is_kb_available")
    async def test_distill_returns_none_skips_write(
        self, mock_avail, mock_agg, mock_distill, mock_write,
    ):
        """提炼失败（返回 None）→不写入"""
        mock_avail.return_value = True
        mock_agg.return_value = [
            {"subcategory": "route_to_image", "title": f"t{i}",
             "content": f"c{i}", "metadata": {}}
            for i in range(6)
        ]
        mock_distill.return_value = None
        await distill_intent_patterns()
        mock_write.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.intent_distiller._write_distilled_rule")
    @patch("services.intent_distiller._distill_for_tool")
    @patch("services.intent_distiller._aggregate_user_patterns")
    @patch("services.intent_distiller.is_kb_available")
    async def test_multiple_tools_grouped(
        self, mock_avail, mock_agg, mock_distill, mock_write,
    ):
        """多工具分组→每组独立提炼"""
        mock_avail.return_value = True
        patterns = []
        for tool in ["route_to_image", "route_to_chat"]:
            for i in range(6):
                patterns.append({
                    "subcategory": tool, "title": f"t{i}",
                    "content": f"c{i}", "metadata": {},
                })
        mock_agg.return_value = patterns
        mock_distill.return_value = {
            "rule": "rule", "keywords": [], "confidence": 0.8,
        }
        mock_write.return_value = "node-1"

        await distill_intent_patterns()
        assert mock_distill.call_count == 2
        assert mock_write.call_count == 2


# ============================================================
# TestCallDistillModel — 单个模型调用
# ============================================================


class TestCallDistillModel:

    @pytest.mark.asyncio
    @patch("services.intent_distiller._ds_client")
    async def test_success_returns_parsed(self, mock_ds):
        """模型正常返回→解析 JSON"""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"rule":"test","keywords":["a"]}'}}],
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_ds.get = AsyncMock(return_value=mock_client)

        result = await _call_distill_model("qwen-turbo", "test prompt")
        assert result is not None
        assert result["rule"] == "test"

    @pytest.mark.asyncio
    @patch("services.intent_distiller._ds_client")
    async def test_timeout_returns_none(self, mock_ds):
        """超时→返回 None"""
        import httpx
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")
        mock_ds.get = AsyncMock(return_value=mock_client)

        result = await _call_distill_model("qwen-turbo", "test prompt")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.intent_distiller._ds_client")
    async def test_error_returns_none(self, mock_ds):
        """其他异常→返回 None"""
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("network error")
        mock_ds.get = AsyncMock(return_value=mock_client)

        result = await _call_distill_model("qwen-turbo", "test prompt")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.intent_distiller._ds_client")
    async def test_invalid_json_response_returns_none(self, mock_ds):
        """模型返回非法 JSON→返回 None"""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "not json"}}],
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_ds.get = AsyncMock(return_value=mock_client)

        result = await _call_distill_model("qwen-turbo", "test prompt")
        assert result is None


# ============================================================
# TestDistillForTool — 降级链调用
# ============================================================


class TestDistillForTool:

    @pytest.mark.asyncio
    @patch("services.intent_distiller._call_distill_model", new_callable=AsyncMock)
    @patch("services.intent_distiller.settings")
    async def test_first_model_success(self, mock_settings, mock_call):
        """第一个模型成功→直接返回"""
        from services.intent_distiller import _distill_for_tool
        mock_settings.intent_distill_model = "qwen-turbo"
        mock_settings.intent_distill_fallback_model = "qwen-plus"
        mock_call.return_value = {"rule": "r", "keywords": ["a"]}

        patterns = [{"metadata": {"original_expression": f"expr{i}"}}
                    for i in range(5)]
        result = await _distill_for_tool("route_to_image", patterns)
        assert result is not None
        assert mock_call.call_count == 1

    @pytest.mark.asyncio
    @patch("services.intent_distiller._call_distill_model", new_callable=AsyncMock)
    @patch("services.intent_distiller.settings")
    async def test_fallback_to_second_model(self, mock_settings, mock_call):
        """第一个模型失败→降级到第二个"""
        from services.intent_distiller import _distill_for_tool
        mock_settings.intent_distill_model = "qwen-turbo"
        mock_settings.intent_distill_fallback_model = "qwen-plus"
        mock_call.side_effect = [None, {"rule": "r", "keywords": []}]

        patterns = [{"metadata": {"original_expression": f"expr{i}"}}
                    for i in range(5)]
        result = await _distill_for_tool("route_to_image", patterns)
        assert result is not None
        assert mock_call.call_count == 2

    @pytest.mark.asyncio
    @patch("services.intent_distiller._call_distill_model", new_callable=AsyncMock)
    @patch("services.intent_distiller.settings")
    async def test_all_models_fail(self, mock_settings, mock_call):
        """所有模型失败→返回 None"""
        from services.intent_distiller import _distill_for_tool
        mock_settings.intent_distill_model = "qwen-turbo"
        mock_settings.intent_distill_fallback_model = "qwen-plus"
        mock_call.return_value = None

        patterns = [{"metadata": {}} for _ in range(5)]
        result = await _distill_for_tool("route_to_image", patterns)
        assert result is None
        assert mock_call.call_count == 2
