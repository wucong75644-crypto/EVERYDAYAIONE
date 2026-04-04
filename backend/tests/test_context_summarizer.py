"""
对话历史摘要压缩功能测试

测试内容：
- context_summarizer: 独立压缩服务（LLM 调用、降级链、prompt 构建）
- _get_context_summary: 从 DB 获取已缓存摘要
- _update_summary_if_needed: 判断是否需要更新 + 生成 + 存储
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockSupabaseClient


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    return MockSupabaseClient()


@pytest.fixture
def chat_handler(mock_db):
    from services.handlers.chat_handler import ChatHandler

    return ChatHandler(db=mock_db)


# ============ Test context_summarizer module ============


class TestBuildSummaryPrompt:
    """测试 _build_summary_prompt 格式化"""

    def test_formats_messages(self):
        from services.context_summarizer import _build_summary_prompt

        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        result = _build_summary_prompt(messages)

        assert "用户：你好" in result
        assert "AI：你好！有什么可以帮你的？" in result

    def test_truncates_long_messages(self):
        from services.context_summarizer import _build_summary_prompt

        messages = [
            {"role": "user", "content": "x" * 300},
        ]
        result = _build_summary_prompt(messages)

        assert len(result) < 300
        assert "..." in result

    def test_empty_messages(self):
        from services.context_summarizer import _build_summary_prompt

        result = _build_summary_prompt([])
        assert result == ""


class TestSummarizeMessages:
    """测试 summarize_messages 降级链"""

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_messages(self):
        from services.context_summarizer import summarize_messages

        result = await summarize_messages([])
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self):
        from services.context_summarizer import summarize_messages

        with patch("services.context_summarizer.settings") as mock_settings:
            mock_settings.dashscope_api_key = None
            result = await summarize_messages([{"role": "user", "content": "hi"}])

        assert result is None

    @pytest.mark.asyncio
    async def test_primary_model_success(self):
        from services.context_summarizer import summarize_messages

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "用户讨论了Python编程"}}]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.is_closed = False

        with patch("services.context_summarizer._ds_client.get", return_value=mock_client), \
             patch("services.context_summarizer.settings") as mock_settings:
            mock_settings.dashscope_api_key = "test-key"
            mock_settings.context_summary_model = "qwen-turbo"
            mock_settings.context_summary_fallback_model = "qwen-plus"
            mock_settings.context_summary_max_chars = 500

            result = await summarize_messages([
                {"role": "user", "content": "我想学Python"},
                {"role": "assistant", "content": "Python是很好的入门语言"},
            ])

        assert result == "用户讨论了Python编程"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_secondary_model(self):
        from services.context_summarizer import summarize_messages
        import httpx

        # 第一次调用超时，第二次成功
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "备用模型生成的摘要"}}]
        }

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = [
            httpx.TimeoutException("timeout"),
            mock_response,
        ]

        with patch("services.context_summarizer._ds_client.get", return_value=mock_client), \
             patch("services.context_summarizer.settings") as mock_settings:
            mock_settings.dashscope_api_key = "test-key"
            mock_settings.context_summary_model = "qwen-turbo"
            mock_settings.context_summary_fallback_model = "qwen-plus"
            mock_settings.context_summary_max_chars = 500

            result = await summarize_messages([
                {"role": "user", "content": "测试消息"},
            ])

        assert result == "备用模型生成的摘要"
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_all_models_fail_returns_none(self):
        from services.context_summarizer import summarize_messages
        import httpx

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        with patch("services.context_summarizer._ds_client.get", return_value=mock_client), \
             patch("services.context_summarizer.settings") as mock_settings:
            mock_settings.dashscope_api_key = "test-key"
            mock_settings.context_summary_model = "qwen-turbo"
            mock_settings.context_summary_fallback_model = "qwen-plus"
            mock_settings.context_summary_max_chars = 500

            result = await summarize_messages([
                {"role": "user", "content": "测试消息"},
            ])

        assert result is None

    @pytest.mark.asyncio
    async def test_truncates_long_summary(self):
        from services.context_summarizer import summarize_messages

        long_summary = "x" * 1000

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": long_summary}}]
        }

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = mock_response

        with patch("services.context_summarizer._ds_client.get", return_value=mock_client), \
             patch("services.context_summarizer.settings") as mock_settings:
            mock_settings.dashscope_api_key = "test-key"
            mock_settings.context_summary_model = "qwen-turbo"
            mock_settings.context_summary_fallback_model = "qwen-plus"
            mock_settings.context_summary_max_chars = 500

            result = await summarize_messages([
                {"role": "user", "content": "测试"},
            ])

        assert len(result) == 500


# ============ Test _get_context_summary (mixin) ============


class TestGetContextSummary:
    """从 DB 获取已缓存摘要"""

    @pytest.mark.asyncio
    async def test_returns_summary_when_cached(self, chat_handler):
        """DB 中有摘要时返回格式化的 prompt"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.single.return_value = mock_table
        mock_table.execute.return_value = MagicMock(
            data={"context_summary": "用户讨论了Python编程和Web开发"}
        )

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        result = await chat_handler._get_context_summary("conv1")

        assert result is not None
        assert "用户讨论了Python编程和Web开发" in result
        assert "以下是之前对话的摘要" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_no_summary(self, chat_handler):
        """DB 中无摘要时返回 None"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.single.return_value = mock_table
        mock_table.execute.return_value = MagicMock(
            data={"context_summary": None}
        )

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        result = await chat_handler._get_context_summary("conv1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self, chat_handler):
        """功能关闭时返回 None"""
        with patch("core.config.settings") as mock_settings:
            mock_settings.context_summary_enabled = False
            result = await chat_handler._get_context_summary("conv1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self, chat_handler):
        """DB 查询失败时降级为 None"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.single.return_value = mock_table
        mock_table.execute.side_effect = Exception("DB error")

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        result = await chat_handler._get_context_summary("conv1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_conversation_not_found(self, chat_handler):
        """对话不存在时返回 None"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.single.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=None)

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        result = await chat_handler._get_context_summary("conv1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_summary_from_prefetched(self, chat_handler):
        """prefetched 有值时直接使用，跳过 DB 查询"""
        chat_handler.db = MagicMock()

        result = await chat_handler._get_context_summary(
            "conv1", prefetched="用户讨论了机器学习"
        )

        assert result is not None
        assert "用户讨论了机器学习" in result
        assert "以下是之前对话的摘要" in result
        # 不应查询 DB
        chat_handler.db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_prefetched_empty_string(self, chat_handler):
        """prefetched 为空字符串时返回 None（跳过 DB）"""
        chat_handler.db = MagicMock()

        result = await chat_handler._get_context_summary(
            "conv1", prefetched=""
        )

        assert result is None
        chat_handler.db.table.assert_not_called()


# ============ Test _update_summary_if_needed (mixin) ============


class TestUpdateSummaryIfNeeded:
    """判断是否需要更新摘要 + 生成 + 存储"""

    def _mock_db_for_update(
        self,
        message_count: int = 25,
        summary_message_count: int = 0,
        messages_data: list = None,
    ):
        """构建 mock DB，支持 conversations 和 messages 两个表"""
        # conversations 查询
        conv_table = MagicMock()
        conv_table.select.return_value = conv_table
        conv_table.eq.return_value = conv_table
        conv_table.single.return_value = conv_table
        conv_table.execute.return_value = MagicMock(
            data={
                "message_count": message_count,
                "summary_message_count": summary_message_count,
            }
        )
        conv_table.update.return_value = conv_table

        # messages 查询
        msg_table = MagicMock()
        msg_table.select.return_value = msg_table
        msg_table.eq.return_value = msg_table
        msg_table.in_.return_value = msg_table
        msg_table.order.return_value = msg_table
        msg_table.execute.return_value = MagicMock(
            data=messages_data or []
        )

        mock_db = MagicMock()

        def table_router(name):
            if name == "conversations":
                return conv_table
            return msg_table

        mock_db.table.side_effect = table_router
        return mock_db, conv_table

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, chat_handler):
        """功能关闭时跳过"""
        with patch("core.config.settings") as mock_settings:
            mock_settings.context_summary_enabled = False
            await chat_handler._update_summary_if_needed("conv1")

    @pytest.mark.asyncio
    async def test_skips_when_few_messages(self, chat_handler):
        """消息数 ≤ chat_context_limit 时跳过"""
        mock_db, _ = self._mock_db_for_update(message_count=8)
        chat_handler.db = mock_db

        await chat_handler._update_summary_if_needed("conv1")

        # 不应查询 messages 表
        calls = [c.args[0] for c in mock_db.table.call_args_list]
        assert "messages" not in calls

    @pytest.mark.asyncio
    async def test_skips_when_summary_is_fresh(self, chat_handler):
        """摘要足够新（新增消息 < update_interval）时跳过"""
        mock_db, _ = self._mock_db_for_update(
            message_count=28, summary_message_count=25
        )
        chat_handler.db = mock_db

        await chat_handler._update_summary_if_needed("conv1")

        # 只查了 conversations，没查 messages
        calls = [c.args[0] for c in mock_db.table.call_args_list]
        assert "messages" not in calls

    @pytest.mark.asyncio
    async def test_generates_summary_when_needed(self, chat_handler):
        """消息数 >20 且无摘要时生成"""
        # 构造 25 条消息
        messages = []
        for i in range(25):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({
                "role": role,
                "content": f"消息{i}",
                "status": "completed",
            })

        mock_db, conv_table = self._mock_db_for_update(
            message_count=25,
            summary_message_count=0,
            messages_data=messages,
        )
        chat_handler.db = mock_db

        with patch(
            "services.context_summarizer.summarize_messages",
            new_callable=AsyncMock,
            return_value="压缩后的摘要内容",
        ) as mock_summarize:
            await chat_handler._update_summary_if_needed("conv1")

        # 压缩前 15 条（25 - 10 = 15）
        mock_summarize.assert_called_once()
        summarized_msgs = mock_summarize.call_args.args[0]
        assert len(summarized_msgs) == 15

        # 验证更新了 conversations 表
        conv_table.update.assert_called_once()
        update_data = conv_table.update.call_args.args[0]
        assert update_data["context_summary"] == "压缩后的摘要内容"
        assert update_data["summary_message_count"] == 25

    @pytest.mark.asyncio
    async def test_updates_stale_summary(self, chat_handler):
        """已有摘要但过期（新增 ≥10 条）时更新"""
        messages = []
        for i in range(35):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({
                "role": role,
                "content": f"消息{i}",
                "status": "completed",
            })

        mock_db, conv_table = self._mock_db_for_update(
            message_count=35,
            summary_message_count=25,
            messages_data=messages,
        )
        chat_handler.db = mock_db

        with patch(
            "services.context_summarizer.summarize_messages",
            new_callable=AsyncMock,
            return_value="更新后的摘要",
        ) as mock_summarize:
            await chat_handler._update_summary_if_needed("conv1")

        # 压缩前 25 条（35 - 10 = 25）
        mock_summarize.assert_called_once()
        summarized_msgs = mock_summarize.call_args.args[0]
        assert len(summarized_msgs) == 25

    @pytest.mark.asyncio
    async def test_skips_when_summarizer_fails(self, chat_handler):
        """压缩服务失败时跳过（不更新 DB）"""
        messages = [
            {"role": "user", "content": f"消息{i}", "status": "completed"}
            for i in range(25)
        ]

        mock_db, conv_table = self._mock_db_for_update(
            message_count=25,
            summary_message_count=0,
            messages_data=messages,
        )
        chat_handler.db = mock_db

        with patch(
            "services.context_summarizer.summarize_messages",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await chat_handler._update_summary_if_needed("conv1")

        conv_table.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_on_db_error(self, chat_handler):
        """DB 异常时不崩溃"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.single.return_value = mock_table
        mock_table.execute.side_effect = Exception("DB error")

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        # 不应抛异常
        await chat_handler._update_summary_if_needed("conv1")
