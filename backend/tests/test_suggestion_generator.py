"""
suggestion_generator 单元测试

覆盖：
1. _parse_suggestions：正常 JSON / code block 包裹 / 解析失败 / 空数组 / 非字符串元素
2. generate_suggestions：降级链（主模型失败→备用→全失败）
3. _call_model：成功 / HTTP 错误 / 响应格式异常
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.suggestion_generator import _parse_suggestions, generate_suggestions, _call_model


# ============================================================
# _parse_suggestions 纯函数测试
# ============================================================


class TestParseSuggestions:
    """测试 JSON 解析逻辑"""

    def test_normal_json_array(self):
        """正常 JSON 数组"""
        result = _parse_suggestions('["按店铺分析", "和前天对比", "导出报表"]', 3)
        assert result == ["按店铺分析", "和前天对比", "导出报表"]

    def test_truncate_to_max_items(self):
        """超出 max_items 时截断"""
        result = _parse_suggestions('["a", "b", "c", "d"]', 2)
        assert result == ["a", "b"]

    def test_code_block_wrapped(self):
        """LLM 用 markdown code block 包裹"""
        text = '```json\n["建议一", "建议二"]\n```'
        result = _parse_suggestions(text, 3)
        assert result == ["建议一", "建议二"]

    def test_code_block_no_language(self):
        """code block 没有语言标记"""
        text = '```\n["a", "b"]\n```'
        result = _parse_suggestions(text, 3)
        assert result == ["a", "b"]

    def test_invalid_json(self):
        """非 JSON 文本"""
        result = _parse_suggestions("这不是JSON", 3)
        assert result is None

    def test_not_array(self):
        """JSON 但不是数组"""
        result = _parse_suggestions('{"key": "value"}', 3)
        assert result is None

    def test_empty_array(self):
        """空数组"""
        result = _parse_suggestions("[]", 3)
        assert result is None

    def test_filter_non_string_elements(self):
        """过滤非字符串元素"""
        result = _parse_suggestions('["有效", 123, null, "也有效"]', 3)
        assert result == ["有效", "也有效"]

    def test_filter_empty_strings(self):
        """过滤空字符串"""
        result = _parse_suggestions('["有效", "", "  ", "也有效"]', 3)
        assert result == ["有效", "也有效"]

    def test_strip_whitespace(self):
        """去除首尾空白"""
        result = _parse_suggestions('[" 有空格 ", "正常"]', 3)
        assert result == ["有空格", "正常"]

    def test_all_filtered_returns_none(self):
        """所有元素都被过滤时返回 None"""
        result = _parse_suggestions('[123, null, ""]', 3)
        assert result is None


# ============================================================
# _call_model 异步测试
# ============================================================


class TestCallModel:
    """测试单模型调用"""

    @pytest.fixture(autouse=True)
    def _mock_client(self):
        """mock DashScope HTTP 客户端"""
        self.mock_response = MagicMock()
        self.mock_response.raise_for_status = MagicMock()
        self.mock_response.json.return_value = {
            "choices": [{"message": {"content": '["建议一", "建议二"]'}}]
        }

        self.mock_client = AsyncMock()
        self.mock_client.post = AsyncMock(return_value=self.mock_response)

        patcher = patch(
            "services.suggestion_generator._ds_client.get",
            new_callable=AsyncMock,
            return_value=self.mock_client,
        )
        patcher.start()
        yield
        patcher.stop()

    async def test_success(self):
        """正常调用返回建议列表"""
        result = await _call_model("qwen3.5-flash", "测试 prompt", 3)
        assert result == ["建议一", "建议二"]
        self.mock_client.post.assert_called_once()

    async def test_http_error(self):
        """HTTP 错误返回 None"""
        self.mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        result = await _call_model("qwen3.5-flash", "测试 prompt", 3)
        assert result is None

    async def test_malformed_response(self):
        """响应格式异常返回 None"""
        self.mock_response.json.return_value = {"choices": []}
        result = await _call_model("qwen3.5-flash", "测试 prompt", 3)
        assert result is None


# ============================================================
# generate_suggestions 降级链测试
# ============================================================


class TestGenerateSuggestions:
    """测试降级链逻辑"""

    @patch("services.suggestion_generator._call_model", new_callable=AsyncMock)
    async def test_first_model_success(self, mock_call):
        """主模型成功，不调备用"""
        mock_call.return_value = ["建议一", "建议二"]
        result = await generate_suggestions("用户问题", "AI 回复")
        assert result == ["建议一", "建议二"]
        assert mock_call.call_count == 1

    @patch("services.suggestion_generator._call_model", new_callable=AsyncMock)
    async def test_fallback_on_first_failure(self, mock_call):
        """主模型失败，备用成功"""
        mock_call.side_effect = [None, ["备用建议"]]
        result = await generate_suggestions("用户问题", "AI 回复")
        assert result == ["备用建议"]
        assert mock_call.call_count == 2

    @patch("services.suggestion_generator._call_model", new_callable=AsyncMock)
    async def test_all_models_fail(self, mock_call):
        """全部失败返回 None"""
        mock_call.return_value = None
        result = await generate_suggestions("用户问题", "AI 回复")
        assert result is None

    @patch("services.suggestion_generator._call_model", new_callable=AsyncMock)
    async def test_empty_query_skipped(self, mock_call):
        """空用户问题不调用模型（在 _generate_suggestions 层面 guard，这里测截断）"""
        result = await generate_suggestions("用户问题", "AI 回复" * 200)
        # ai_reply 被截断到 500 字
        call_args = mock_call.call_args
        user_prompt = call_args[0][1]
        assert len(user_prompt) < 1000  # prompt 不应过长

    @patch("services.suggestion_generator._call_model", new_callable=AsyncMock)
    async def test_max_items_passed_through(self, mock_call):
        """max_items 传递到 _call_model"""
        mock_call.return_value = ["a"]
        await generate_suggestions("q", "r", max_items=5)
        call_args = mock_call.call_args
        assert call_args[0][2] == 5  # 第三个参数是 max_items
