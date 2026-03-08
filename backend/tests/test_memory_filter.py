"""记忆智能过滤器单元测试（评分制）"""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from services.memory_filter import (
    RELEVANCE_THRESHOLD,
    _build_filter_prompt,
    _parse_score_response,
    filter_memories,
)


# ============ 纯函数测试 ============


class TestBuildFilterPrompt:
    """_build_filter_prompt 测试"""

    def test_basic(self):
        memories = [
            {"memory": "用户是Python开发者", "score": 0.82},
            {"memory": "用户喜欢咖啡", "score": 0.52},
        ]
        result = _build_filter_prompt("写个爬虫", memories)

        assert "用户问题：写个爬虫" in result
        assert "Doc 1: 用户是Python开发者" in result
        assert "Doc 2: 用户喜欢咖啡" in result

    def test_no_score(self):
        memories = [{"memory": "用户是开发者"}]
        result = _build_filter_prompt("你好", memories)
        assert "Doc 1: 用户是开发者" in result


class TestParseScoreResponse:
    """_parse_score_response 测试"""

    def test_normal_format(self):
        text = "Doc: 1, Relevance: 8\nDoc: 2, Relevance: 3\nDoc: 3, Relevance: 9"
        result = _parse_score_response(text, 3)
        # 按分数降序
        assert result == [(2, 9), (0, 8), (1, 3)]

    def test_no_colon_format(self):
        """兼容 'Doc 1, Relevance: 8' 格式"""
        text = "Doc 1, Relevance: 8\nDoc 2, Relevance: 5"
        result = _parse_score_response(text, 2)
        assert result == [(0, 8), (1, 5)]

    def test_single_item(self):
        result = _parse_score_response("Doc: 1, Relevance: 7", 1)
        assert result == [(0, 7)]

    def test_out_of_bounds_filtered(self):
        text = "Doc: 1, Relevance: 8\nDoc: 10, Relevance: 9"
        result = _parse_score_response(text, 3)
        assert result == [(0, 8)]

    def test_invalid_text(self):
        assert _parse_score_response("invalid text", 3) is None

    def test_empty_text(self):
        assert _parse_score_response("", 3) is None

    def test_score_out_of_range_filtered(self):
        """分数不在 1-10 范围内被过滤"""
        text = "Doc: 1, Relevance: 11\nDoc: 2, Relevance: 0\nDoc: 3, Relevance: 5"
        result = _parse_score_response(text, 3)
        assert result == [(2, 5)]

    def test_whitespace_tolerance(self):
        text = "  Doc:  1 , Relevance:  8  "
        result = _parse_score_response(text, 1)
        assert result == [(0, 8)]


# ============ filter_memories 集成测试 ============


class TestFilterMemories:
    """filter_memories 降级链测试"""

    def _make_memories(self, count: int):
        return [
            {"id": f"m{i}", "memory": f"记忆{i}", "score": 0.9 - i * 0.1}
            for i in range(count)
        ]

    def _make_score_response(self, scores: list[tuple[int, int]]) -> str:
        """构建评分响应文本"""
        lines = [f"Doc: {doc}, Relevance: {score}" for doc, score in scores]
        return "\n".join(lines)

    @pytest.mark.asyncio
    async def test_empty_memories_passthrough(self):
        result = await filter_memories("查询", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_query_passthrough(self):
        memories = self._make_memories(5)
        result = await filter_memories("", memories)
        assert result == memories

    @pytest.mark.asyncio
    async def test_three_or_less_skip_filter(self):
        """≤3 条直接返回，不调千问"""
        memories = self._make_memories(3)
        result = await filter_memories("查询", memories)
        assert result == memories

    @pytest.mark.asyncio
    async def test_primary_model_success(self):
        """主模型成功：只保留分数 ≥ 阈值的记忆"""
        memories = self._make_memories(5)
        # Doc 1: 9分(保留), Doc 2: 3分(过滤), Doc 3: 8分(保留),
        # Doc 4: 2分(过滤), Doc 5: 7分(保留)
        score_text = self._make_score_response([
            (1, 9), (2, 3), (3, 8), (4, 2), (5, 7),
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": score_text}}]
        }

        with patch(
            "services.memory_filter._ds_client.get",
            return_value=AsyncMock(post=AsyncMock(return_value=mock_response)),
        ):
            result = await filter_memories("查询", memories)

        assert len(result) == 3
        # 按分数降序返回
        assert result[0]["id"] == "m0"  # Doc 1, 9分
        assert result[1]["id"] == "m2"  # Doc 3, 8分
        assert result[2]["id"] == "m4"  # Doc 5, 7分

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_success(self):
        """主模型失败，备用模型成功"""
        memories = self._make_memories(5)
        score_text = self._make_score_response([(1, 8), (2, 9)])

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.raise_for_status = MagicMock()
        ok_response.json.return_value = {
            "choices": [{"message": {"content": score_text}}]
        }

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("primary failed")
            return ok_response

        mock_client = AsyncMock(post=mock_post)
        with patch(
            "services.memory_filter._ds_client.get",
            return_value=mock_client,
        ):
            result = await filter_memories("查询", memories)

        assert len(result) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_models_fail_returns_original(self):
        """所有模型都失败，返回原始列表"""
        memories = self._make_memories(5)

        async def mock_post(*args, **kwargs):
            raise Exception("all failed")

        mock_client = AsyncMock(post=mock_post)
        with patch(
            "services.memory_filter._ds_client.get",
            return_value=mock_client,
        ):
            result = await filter_memories("查询", memories)

        assert result == memories

    @pytest.mark.asyncio
    async def test_all_below_threshold_keeps_top1(self):
        """所有分数都低于阈值时保留最高分的一条"""
        memories = self._make_memories(5)
        # 所有分数都 < 7
        score_text = self._make_score_response([
            (1, 5), (2, 3), (3, 6), (4, 2), (5, 4),
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": score_text}}]
        }

        with patch(
            "services.memory_filter._ds_client.get",
            return_value=AsyncMock(post=AsyncMock(return_value=mock_response)),
        ):
            result = await filter_memories("查询", memories)

        assert len(result) == 1
        assert result[0]["id"] == "m2"  # Doc 3 得分最高(6分)

    @pytest.mark.asyncio
    async def test_no_api_key_passthrough(self):
        """无 API key 时直接返回原始列表"""
        memories = self._make_memories(5)
        with patch("services.memory_filter.settings") as mock_settings:
            mock_settings.dashscope_api_key = None
            result = await filter_memories("查询", memories)
        assert result == memories

    @pytest.mark.asyncio
    async def test_result_sorted_by_score_desc(self):
        """结果按分数降序排列"""
        memories = self._make_memories(5)
        score_text = self._make_score_response([
            (1, 7), (2, 10), (3, 8), (4, 3), (5, 9),
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": score_text}}]
        }

        with patch(
            "services.memory_filter._ds_client.get",
            return_value=AsyncMock(post=AsyncMock(return_value=mock_response)),
        ):
            result = await filter_memories("查询", memories)

        assert len(result) == 4
        assert result[0]["id"] == "m1"  # 10分
        assert result[1]["id"] == "m4"  # 9分
        assert result[2]["id"] == "m2"  # 8分
        assert result[3]["id"] == "m0"  # 7分
