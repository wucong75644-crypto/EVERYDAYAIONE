"""
对话上下文注入功能测试

测试 ChatHandler 的历史消息上下文构建：
- _extract_text_from_content: 从 DB content 字段提取纯文本
- _build_context_messages: 构建对话历史上下文
- _stream_generate 集成: 验证上下文正确注入到消息列表
"""

import json

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


# ============ Test _extract_text_from_content ============


class TestExtractTextFromContent:
    """从 DB content 字段提取纯文本"""

    def test_plain_string(self, chat_handler):
        assert chat_handler._extract_text_from_content("hello world") == "hello world"

    def test_string_with_whitespace(self, chat_handler):
        assert chat_handler._extract_text_from_content("  hello  ") == "hello"

    def test_list_with_single_text_part(self, chat_handler):
        content = [{"type": "text", "text": "hello"}]
        assert chat_handler._extract_text_from_content(content) == "hello"

    def test_list_with_multiple_text_parts(self, chat_handler):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        assert chat_handler._extract_text_from_content(content) == "hello world"

    def test_list_with_image_only(self, chat_handler):
        content = [{"type": "image", "url": "https://example.com/img.png"}]
        assert chat_handler._extract_text_from_content(content) == ""

    def test_list_with_video_only(self, chat_handler):
        content = [{"type": "video", "url": "https://example.com/v.mp4"}]
        assert chat_handler._extract_text_from_content(content) == ""

    def test_mixed_content_extracts_text_only(self, chat_handler):
        content = [
            {"type": "text", "text": "画一只猫"},
            {"type": "image", "url": "https://example.com/cat.png"},
        ]
        assert chat_handler._extract_text_from_content(content) == "画一只猫"

    def test_empty_list(self, chat_handler):
        assert chat_handler._extract_text_from_content([]) == ""

    def test_none_input(self, chat_handler):
        assert chat_handler._extract_text_from_content(None) == ""

    def test_integer_input(self, chat_handler):
        assert chat_handler._extract_text_from_content(42) == ""

    def test_json_string_array(self, chat_handler):
        """Supabase 可能返回 JSONB 为字符串"""
        content = json.dumps([{"type": "text", "text": "from json"}])
        assert chat_handler._extract_text_from_content(content) == "from json"

    def test_json_string_with_mixed_parts(self, chat_handler):
        content = json.dumps([
            {"type": "text", "text": "描述"},
            {"type": "image", "url": "https://img.png"},
        ])
        assert chat_handler._extract_text_from_content(content) == "描述"

    def test_empty_text_parts_skipped(self, chat_handler):
        content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "  "},
            {"type": "text", "text": "valid"},
        ]
        assert chat_handler._extract_text_from_content(content) == "valid"

    def test_non_json_string(self, chat_handler):
        """普通文本字符串（非 JSON）直接返回"""
        assert chat_handler._extract_text_from_content("普通文本") == "普通文本"

    def test_invalid_json_string(self, chat_handler):
        """无效 JSON 字符串当作普通文本"""
        assert chat_handler._extract_text_from_content("{broken") == "{broken"


# ============ Test _build_context_messages ============


def _make_msg(role, text, status="completed", conversation_id="conv1"):
    """构造 messages 表数据行"""
    if isinstance(text, str):
        content = [{"type": "text", "text": text}]
    else:
        content = text
    return {
        "role": role,
        "content": content,
        "status": status,
        "conversation_id": conversation_id,
        "created_at": "2026-03-06T10:00:00Z",
    }


class TestBuildContextMessages:
    """构建对话历史上下文"""

    def test_normal_history(self, chat_handler, mock_db):
        """正常返回历史消息（正序），当前消息被去重"""
        # mock DB 返回 DESC 顺序（最新在前）
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前问题"),       # 最新（将被去重）
            _make_msg("assistant", "AI回复2"),
            _make_msg("user", "第二个问题"),
            _make_msg("assistant", "AI回复1"),
            _make_msg("user", "第一个问题"),
        ])

        result = chat_handler._build_context_messages("conv1", "当前问题")

        assert len(result) == 4
        assert result[0] == {"role": "user", "content": "第一个问题"}
        assert result[1] == {"role": "assistant", "content": "AI回复1"}
        assert result[2] == {"role": "user", "content": "第二个问题"}
        assert result[3] == {"role": "assistant", "content": "AI回复2"}

    def test_empty_history(self, chat_handler, mock_db):
        """新对话，无历史消息"""
        mock_db.set_table_data("messages", [])

        result = chat_handler._build_context_messages("conv1", "hello")
        assert result == []

    def test_skips_image_only_messages(self, chat_handler, mock_db):
        """跳过只有图片没有文本的消息"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "看看这张图"),
            _make_msg("assistant", [{"type": "image", "url": "https://img.png"}]),
            _make_msg("user", "第一条消息"),
        ])

        result = chat_handler._build_context_messages("conv1", "看看这张图")

        # assistant 的纯图片消息被跳过
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "第一条消息"}

    def test_extracts_text_from_mixed_content(self, chat_handler, mock_db):
        """混合内容只提取文本部分"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("user", [
                {"type": "text", "text": "画一只猫"},
                {"type": "image", "url": "https://cat.png"},
            ]),
        ])

        result = chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "画一只猫"}

    def test_dedup_removes_trailing_current_message(self, chat_handler, mock_db):
        """去除末尾与当前消息重复的 user 消息"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "hello"),       # 当前消息（重复）
            _make_msg("assistant", "world"),
        ])

        result = chat_handler._build_context_messages("conv1", "hello")

        # 只保留 assistant 消息
        assert len(result) == 1
        assert result[0] == {"role": "assistant", "content": "world"}

    def test_dedup_keeps_non_matching_tail(self, chat_handler, mock_db):
        """末尾 user 消息与当前不同时保留"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "不同的消息"),
            _make_msg("assistant", "reply"),
        ])

        result = chat_handler._build_context_messages("conv1", "当前消息")

        assert len(result) == 2
        assert result[0]["content"] == "reply"
        assert result[1]["content"] == "不同的消息"

    def test_dedup_does_not_remove_assistant_tail(self, chat_handler, mock_db):
        """末尾是 assistant 消息时不去重"""
        mock_db.set_table_data("messages", [
            _make_msg("assistant", "最后回复"),
            _make_msg("user", "问题"),
        ])

        result = chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 2

    def test_context_limit_zero_returns_empty(self, chat_handler, mock_db):
        """chat_context_limit=0 时返回空"""
        mock_db.set_table_data("messages", [_make_msg("user", "hello")])

        with patch("core.config.settings") as mock_settings:
            mock_settings.chat_context_limit = 0
            result = chat_handler._build_context_messages("conv1", "hello")

        assert result == []

    def test_db_error_graceful_degradation(self, chat_handler):
        """DB 查询失败时降级为空"""
        broken_table = MagicMock()
        broken_table.select.return_value = broken_table
        broken_table.eq.return_value = broken_table
        broken_table.order.return_value = broken_table
        broken_table.limit.return_value = broken_table
        broken_table.execute.side_effect = Exception("DB connection failed")

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = broken_table

        result = chat_handler._build_context_messages("conv1", "hello")
        assert result == []

    def test_filters_by_conversation_and_status(self, chat_handler):
        """验证 DB 查询使用了正确的过滤条件"""
        # 使用 MagicMock 验证查询链是否正确构建
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            _make_msg("user", "msg1"),
        ])

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        chat_handler._build_context_messages("conv1", "current")

        # 验证查询链参数
        chat_handler.db.table.assert_called_once_with("messages")
        mock_table.select.assert_called_once_with("role, content, status, created_at")
        eq_calls = mock_table.eq.call_args_list
        assert ("conversation_id", "conv1") in [c.args for c in eq_calls]
        assert ("status", "completed") in [c.args for c in eq_calls]
        mock_table.order.assert_called_once_with("created_at", desc=True)

    def test_filters_out_system_role(self, chat_handler, mock_db):
        """过滤掉 system role 的消息"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("system", "system prompt"),
            _make_msg("user", "用户消息"),
        ])

        result = chat_handler._build_context_messages("conv1", "当前")

        # system 消息被过滤
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "用户消息"}

    def test_retry_scenario_no_new_user_message(self, chat_handler, mock_db):
        """retry 场景：没有新 user 消息，上下文保留完整历史"""
        mock_db.set_table_data("messages", [
            # retry 不创建新 user 消息，最近的 completed user 消息就是原始问题
            _make_msg("user", "画一只猫"),
            _make_msg("assistant", "之前的回复"),
            _make_msg("user", "你好"),
        ])

        # retry 时 current_text 与历史中最后一条 user 消息相同
        result = chat_handler._build_context_messages("conv1", "画一只猫")

        # 去重移除了末尾的"画一只猫"，但之前的历史保留
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "你好"}
        assert result[1] == {"role": "assistant", "content": "之前的回复"}


# ============ Test _stream_generate context injection ============


def _make_mock_chunk(content="hi", prompt_tokens=0, completion_tokens=0):
    """构造 mock stream chunk"""
    chunk = MagicMock()
    chunk.content = content
    chunk.prompt_tokens = prompt_tokens
    chunk.completion_tokens = completion_tokens
    return chunk


class TestStreamGenerateContextInjection:
    """验证 _stream_generate 正确组装消息列表"""

    @pytest.fixture
    def mock_adapter(self):
        """Mock chat adapter（estimate_cost_unified 是同步方法）"""
        adapter = AsyncMock()
        adapter.estimate_cost_unified = MagicMock(
            return_value=MagicMock(estimated_credits=0)
        )
        adapter.close = AsyncMock()
        return adapter

    def _make_capture_stream(self, captured_messages):
        """创建捕获 messages 的 mock stream_chat"""
        async def capture_stream(messages, **kwargs):
            captured_messages.extend(messages)
            yield _make_mock_chunk("reply", 10, 5)
        return capture_stream

    @pytest.mark.asyncio
    async def test_context_injected_between_memory_and_current(
        self, chat_handler, mock_db, mock_adapter
    ):
        """上下文应插入在 memory system prompt 和当前用户消息之间"""
        # mock 数据按 DESC 顺序（最新在前），_build_context_messages 会反转
        mock_db.set_table_data("messages", [
            _make_msg("assistant", "你好！有什么可以帮你的？"),
            _make_msg("user", "你好"),
        ])

        captured = []
        mock_adapter.stream_chat = self._make_capture_stream(captured)

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws, \
             patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(chat_handler, "_build_memory_prompt", return_value="你是AI助手"), \
             patch.object(chat_handler, "on_complete", new_callable=AsyncMock), \
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock):
            mock_ws.send_to_task_subscribers = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "今天天气怎么样"}],
                model_id="gemini-3-flash",
            )

        # 验证消息顺序: system → context history → current user
        assert len(captured) == 4
        assert captured[0] == {"role": "system", "content": "你是AI助手"}
        assert captured[1] == {"role": "user", "content": "你好"}
        assert captured[2] == {"role": "assistant", "content": "你好！有什么可以帮你的？"}
        assert captured[3]["role"] == "user"
        assert captured[3]["content"] == "今天天气怎么样"

    @pytest.mark.asyncio
    async def test_context_without_memory(self, chat_handler, mock_db, mock_adapter):
        """无记忆时：上下文 + 当前消息"""
        mock_db.set_table_data("messages", [
            _make_msg("assistant", "之前的回答"),
            _make_msg("user", "之前的问题"),
        ])

        captured = []
        mock_adapter.stream_chat = self._make_capture_stream(captured)

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws, \
             patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(chat_handler, "_build_memory_prompt", return_value=None), \
             patch.object(chat_handler, "on_complete", new_callable=AsyncMock), \
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock):
            mock_ws.send_to_task_subscribers = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "新问题"}],
                model_id="gemini-3-flash",
            )

        assert len(captured) == 3
        assert captured[0] == {"role": "user", "content": "之前的问题"}
        assert captured[1] == {"role": "assistant", "content": "之前的回答"}
        assert captured[2]["role"] == "user"
        assert captured[2]["content"] == "新问题"

    @pytest.mark.asyncio
    async def test_no_context_new_conversation(
        self, chat_handler, mock_db, mock_adapter
    ):
        """新对话无历史：只有当前消息"""
        mock_db.set_table_data("messages", [])

        captured = []
        mock_adapter.stream_chat = self._make_capture_stream(captured)

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws, \
             patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(chat_handler, "_build_memory_prompt", return_value=None), \
             patch.object(chat_handler, "on_complete", new_callable=AsyncMock), \
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock):
            mock_ws.send_to_task_subscribers = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "第一条消息"}],
                model_id="gemini-3-flash",
            )

        assert len(captured) == 1
        assert captured[0]["role"] == "user"
        assert captured[0]["content"] == "第一条消息"

    @pytest.mark.asyncio
    async def test_context_with_vqa_image(self, chat_handler, mock_db, mock_adapter):
        """带图片的 VQA 模式：上下文是纯文本，当前消息保留图片"""
        mock_db.set_table_data("messages", [
            _make_msg("assistant", "之前的回复"),
            _make_msg("user", "之前的对话"),
        ])

        captured = []
        mock_adapter.stream_chat = self._make_capture_stream(captured)

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws, \
             patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(chat_handler, "_build_memory_prompt", return_value=None), \
             patch.object(chat_handler, "on_complete", new_callable=AsyncMock), \
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock):
            mock_ws.send_to_task_subscribers = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[
                    {"type": "text", "text": "这张图是什么"},
                    {"type": "image", "url": "https://img.png"},
                ],
                model_id="gemini-3-flash",
            )

        # 上下文是纯文本，当前消息包含图片
        assert len(captured) == 3
        assert captured[0] == {"role": "user", "content": "之前的对话"}
        assert captured[1] == {"role": "assistant", "content": "之前的回复"}
        # 当前消息是多模态格式
        assert captured[2]["role"] == "user"
        assert isinstance(captured[2]["content"], list)
