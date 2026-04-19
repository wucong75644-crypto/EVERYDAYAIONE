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


def _make_msg(role, text, status="completed", conversation_id="conv1", generation_params=None):
    """构造 messages 表数据行"""
    if isinstance(text, str):
        content = [{"type": "text", "text": text}]
    else:
        content = text
    msg = {
        "role": role,
        "content": content,
        "status": status,
        "conversation_id": conversation_id,
        "created_at": "2026-03-06T10:00:00Z",  # UTC → CN_TZ = 03-06 18:00
        "generation_params": generation_params,
    }
    return msg


# 历史消息时间戳前缀（mock created_at 固定为 UTC 10:00 → CN 18:00）
_TS = "[03-06 18:00] "


def _ts(text: str) -> str:
    """给预期文本加上时间戳前缀"""
    return f"{_TS}{text}"


# ============ Test _extract_user_query ============


class TestExtractUserQuery:
    """从 task.request_params 提取用户原始问题"""

    def test_dict_with_content(self, chat_handler):
        task = {"request_params": {"content": "昨天订单多少"}}
        assert chat_handler._extract_user_query(task) == "昨天订单多少"

    def test_json_string_params(self, chat_handler):
        task = {"request_params": '{"content": "查一下库存"}'}
        assert chat_handler._extract_user_query(task) == "查一下库存"

    def test_missing_request_params(self, chat_handler):
        task = {}
        assert chat_handler._extract_user_query(task) == ""

    def test_none_request_params(self, chat_handler):
        task = {"request_params": None}
        assert chat_handler._extract_user_query(task) == ""

    def test_missing_content_key(self, chat_handler):
        task = {"request_params": {"model_id": "gpt-4"}}
        assert chat_handler._extract_user_query(task) == ""

    def test_truncate_long_content(self, chat_handler):
        task = {"request_params": {"content": "很长的文本" * 100}}
        result = chat_handler._extract_user_query(task)
        assert len(result) <= 200


class TestBuildContextMessages:
    """构建对话历史上下文"""

    @pytest.mark.asyncio
    async def test_normal_history(self, chat_handler, mock_db):
        """正常返回历史消息（正序），当前消息被去重"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前问题"),
            _make_msg("assistant", "AI回复2"),
            _make_msg("user", "第二个问题"),
            _make_msg("assistant", "AI回复1"),
            _make_msg("user", "第一个问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前问题")

        assert len(result) == 4
        assert result[0] == {"role": "user", "content": _ts("第一个问题")}
        assert result[1] == {"role": "assistant", "content": _ts("AI回复1")}
        assert result[2] == {"role": "user", "content": _ts("第二个问题")}
        assert result[3] == {"role": "assistant", "content": _ts("AI回复2")}

    @pytest.mark.asyncio
    async def test_empty_history(self, chat_handler, mock_db):
        """新对话，无历史消息"""
        mock_db.set_table_data("messages", [])

        result = await chat_handler._build_context_messages("conv1", "hello")
        assert result == []

    @pytest.mark.asyncio
    async def test_includes_image_only_messages(self, chat_handler, mock_db):
        """图片消息（无文本）也被包含，以多模态格式传递"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "看看这张图"),
            _make_msg("assistant", [{"type": "image", "url": "https://img.png"}]),
            _make_msg("user", "第一条消息"),
        ])

        result = await chat_handler._build_context_messages("conv1", "看看这张图")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": _ts("第一条消息")}
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == [
            {"type": "image_url", "image_url": {"url": "https://img.png"}},
        ]

    @pytest.mark.asyncio
    async def test_mixed_content_includes_text_and_image(self, chat_handler, mock_db):
        """混合内容同时包含文本和图片（多模态格式）"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("user", [
                {"type": "text", "text": "画一只猫"},
                {"type": "image", "url": "https://cat.png"},
            ]),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == [
            {"type": "text", "text": _ts("画一只猫")},
            {"type": "image_url", "image_url": {"url": "https://cat.png"}},
        ]

    @pytest.mark.asyncio
    async def test_dedup_removes_trailing_current_message(self, chat_handler, mock_db):
        """去除末尾与当前消息重复的 user 消息"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "hello"),
            _make_msg("assistant", "world"),
        ])

        result = await chat_handler._build_context_messages("conv1", "hello")

        assert len(result) == 1
        assert result[0] == {"role": "assistant", "content": _ts("world")}

    @pytest.mark.asyncio
    async def test_dedup_keeps_non_matching_tail(self, chat_handler, mock_db):
        """末尾 user 消息与当前不同时保留"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "不同的消息"),
            _make_msg("assistant", "reply"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前消息")

        assert len(result) == 2
        assert result[0]["content"] == _ts("reply")
        assert result[1]["content"] == _ts("不同的消息")

    @pytest.mark.asyncio
    async def test_dedup_does_not_remove_assistant_tail(self, chat_handler, mock_db):
        """末尾是 assistant 消息时不去重"""
        mock_db.set_table_data("messages", [
            _make_msg("assistant", "最后回复"),
            _make_msg("user", "问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_token_budget_zero_returns_empty(self, chat_handler, mock_db):
        """context_history_token_budget=0 时返回空（禁用上下文加载）"""
        mock_db.set_table_data("messages", [_make_msg("user", "hello")])

        with patch("core.config.settings") as mock_settings:
            mock_settings.context_history_token_budget = 0
            mock_settings.chat_context_max_images = 5
            result = await chat_handler._build_context_messages("conv1", "hello")

        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_graceful_degradation(self, chat_handler):
        """DB 查询失败时降级为空"""
        broken_table = MagicMock()
        broken_table.select.return_value = broken_table
        broken_table.eq.return_value = broken_table
        broken_table.in_.return_value = broken_table
        broken_table.order.return_value = broken_table
        broken_table.limit.return_value = broken_table
        broken_table.execute.side_effect = Exception("DB connection failed")

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = broken_table

        result = await chat_handler._build_context_messages("conv1", "hello")
        assert result == []

    @pytest.mark.asyncio
    async def test_query_filters_role_and_status(self, chat_handler):
        """验证 DB 查询包含 role 过滤（in_）和 status 过滤（eq）"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.range.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[
            _make_msg("user", "msg1"),
        ])

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        await chat_handler._build_context_messages("conv1", "current")

        chat_handler.db.table.assert_called_once_with("messages")
        mock_table.select.assert_called_once_with("role, content, status, created_at, generation_params")
        eq_calls = mock_table.eq.call_args_list
        assert ("conversation_id", "conv1") in [c.args for c in eq_calls]
        assert ("status", "completed") in [c.args for c in eq_calls]
        mock_table.in_.assert_called_once_with("role", ["user", "assistant"])
        mock_table.order.assert_called_once_with("created_at", desc=True)

    @pytest.mark.asyncio
    async def test_filters_out_system_role_at_db_level(self, chat_handler):
        """system role 在 DB 层被 in_ 过滤（验证查询链包含 role 过滤）"""
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.in_.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.range.return_value = mock_table
        # 模拟 DB 已过滤 system role，只返回 user/assistant
        mock_table.execute.return_value = MagicMock(data=[
            _make_msg("user", "当前"),
            _make_msg("user", "用户消息"),
        ])

        chat_handler.db = MagicMock()
        chat_handler.db.table.return_value = mock_table

        result = await chat_handler._build_context_messages("conv1", "当前")

        mock_table.in_.assert_called_once_with("role", ["user", "assistant"])
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": _ts("用户消息")}

    @pytest.mark.asyncio
    async def test_retry_scenario_no_new_user_message(self, chat_handler, mock_db):
        """retry 场景：没有新 user 消息，上下文保留完整历史"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "画一只猫"),
            _make_msg("assistant", "之前的回复"),
            _make_msg("user", "你好"),
        ])

        result = await chat_handler._build_context_messages("conv1", "画一只猫")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": _ts("你好")}
        assert result[1] == {"role": "assistant", "content": _ts("之前的回复")}

    @pytest.mark.asyncio
    async def test_token_budget_truncates_oldest(self, chat_handler, mock_db):
        """超过 token 预算时优先保留最新消息，丢弃最旧的"""
        # 8000 token = 20000 字符，每条消息 12000 字符（≈4800 token）
        # 两条共 9600 token > 8000 预算
        long_text = "x" * 12000
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("assistant", long_text),  # 最新（DESC 第一条）
            _make_msg("user", long_text),        # 最旧（DESC 第二条，超预算被截断）
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        # 从新→旧遍历：最新 assistant 4800 token < 8000 保留，旧 user 累计超限被截断
        assert len(result) == 1
        assert result[0]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_char_limit_keeps_all_within_budget(self, chat_handler, mock_db):
        """所有消息总字符在上限内时全部保留"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("assistant", "短回复"),
            _make_msg("user", "短问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": _ts("短问题")}
        assert result[1] == {"role": "assistant", "content": _ts("短回复")}

    @pytest.mark.asyncio
    async def test_image_limit_caps_total_images(self, chat_handler, mock_db):
        """图片数量超过 chat_context_max_images 时截断"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            # 最新消息：3 张图（DESC 第一条）
            _make_msg("user", [
                {"type": "text", "text": "三张图"},
                {"type": "image", "url": "https://img1.png"},
                {"type": "image", "url": "https://img2.png"},
                {"type": "image", "url": "https://img3.png"},
            ]),
            # 较旧消息：2 张图（DESC 第二条）
            _make_msg("assistant", [
                {"type": "text", "text": "生成了两张"},
                {"type": "image", "url": "https://img4.png"},
                {"type": "image", "url": "https://img5.png"},
            ]),
        ])

        with patch("core.config.settings") as mock_settings:
            mock_settings.context_history_token_budget = 8000
            mock_settings.chat_context_max_images = 4  # 只允许 4 张

            result = await chat_handler._build_context_messages("conv1", "当前")

        # 最新消息 3 张全部保留，较旧消息只保留 1 张（4-3=1）
        assert len(result) == 2
        # 旧消息（正序第一条）：只有 1 张图片
        older = result[0]
        assert older["role"] == "assistant"
        image_parts = [p for p in older["content"] if p.get("type") == "image_url"]
        assert len(image_parts) == 1
        # 新消息（正序第二条）：3 张图片
        newer = result[1]
        assert newer["role"] == "user"
        image_parts = [p for p in newer["content"] if p.get("type") == "image_url"]
        assert len(image_parts) == 3

    @pytest.mark.asyncio
    async def test_no_image_messages_stay_text_format(self, chat_handler, mock_db):
        """纯文本消息保持字符串格式（不变为多模态列表）"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("assistant", "纯文本回复"),
            _make_msg("user", "纯文本问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 2
        # 纯文本消息 content 是字符串，不是列表
        assert isinstance(result[0]["content"], str)
        assert isinstance(result[1]["content"], str)

    @pytest.mark.asyncio
    async def test_dedup_works_with_multimodal_content(self, chat_handler, mock_db):
        """去重逻辑对多模态格式（含图片）的消息也生效"""
        mock_db.set_table_data("messages", [
            _make_msg("user", [
                {"type": "text", "text": "当前消息"},
                {"type": "image", "url": "https://img.png"},
            ]),
            _make_msg("assistant", "回复"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前消息")

        # 末尾 user 消息文本与 current_text 相同，应被去重
        assert len(result) == 1
        assert result[0] == {"role": "assistant", "content": _ts("回复")}

    @pytest.mark.asyncio
    async def test_image_null_url_skipped(self, chat_handler, mock_db):
        """图片 URL 为 null 时跳过（生成中的占位图片）"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前"),
            _make_msg("assistant", [
                {"type": "text", "text": "正在生成"},
                {"type": "image", "url": None},
            ]),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前")

        assert len(result) == 1
        # url=None 的图片被跳过，只剩文本 → 纯文本格式
        assert result[0] == {"role": "assistant", "content": _ts("正在生成")}


# ============ Test tool_digest 注入 ============


class TestToolDigestInjection:
    """_build_context_messages 加载带 tool_digest 的 assistant 消息时注入注解"""

    @pytest.mark.asyncio
    async def test_digest_injected_into_assistant_text(self, chat_handler, mock_db):
        """assistant 消息带 tool_digest → 注入 [上轮工具执行记录]"""
        digest = {
            "tools": [{"name": "erp_agent", "hint": "查订单", "ok": True, "staged": "tool_result_erp_agent_a1b2.txt"}],
            "staging_dir": "staging/conv1",
        }
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前问题"),
            _make_msg("assistant", "回答内容", generation_params={"type": "chat", "tool_digest": digest}),
            _make_msg("user", "之前的问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前问题")

        # 找到 assistant 消息
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        content = assistant_msgs[0]["content"]
        assert "[上轮工具执行记录]" in content
        assert "erp_agent" in content
        assert "tool_result_erp_agent_a1b2.txt" in content

    @pytest.mark.asyncio
    async def test_no_digest_no_injection(self, chat_handler, mock_db):
        """assistant 消息无 tool_digest → 不注入"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前问题"),
            _make_msg("assistant", "回答内容", generation_params={"type": "chat"}),
            _make_msg("user", "之前的问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前问题")

        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "[上轮工具执行记录]" not in assistant_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_digest_with_null_generation_params(self, chat_handler, mock_db):
        """generation_params 为 None → 不 crash"""
        mock_db.set_table_data("messages", [
            _make_msg("user", "当前问题"),
            _make_msg("assistant", "回答内容", generation_params=None),
            _make_msg("user", "之前的问题"),
        ])

        result = await chat_handler._build_context_messages("conv1", "当前问题")
        assert len([m for m in result if m["role"] == "assistant"]) == 1


# ============ Test _extract_image_urls_from_content ============


class TestExtractImageUrlsFromContent:
    """从 DB content 字段提取图片 URL"""

    def test_list_with_images(self, chat_handler):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image", "url": "https://img1.png"},
            {"type": "image", "url": "https://img2.png"},
        ]
        assert chat_handler._extract_image_urls_from_content(content) == [
            "https://img1.png", "https://img2.png",
        ]

    def test_no_images(self, chat_handler):
        content = [{"type": "text", "text": "hello"}]
        assert chat_handler._extract_image_urls_from_content(content) == []

    def test_json_string(self, chat_handler):
        content = json.dumps([
            {"type": "image", "url": "https://img.png"},
        ])
        assert chat_handler._extract_image_urls_from_content(content) == [
            "https://img.png",
        ]

    def test_plain_string(self, chat_handler):
        assert chat_handler._extract_image_urls_from_content("hello") == []

    def test_null_url_skipped(self, chat_handler):
        content = [{"type": "image", "url": None}]
        assert chat_handler._extract_image_urls_from_content(content) == []

    def test_empty_list(self, chat_handler):
        assert chat_handler._extract_image_urls_from_content([]) == []

    def test_none_input(self, chat_handler):
        assert chat_handler._extract_image_urls_from_content(None) == []


# ============ Test _stream_generate context injection ============


def _make_mock_chunk(content="hi", prompt_tokens=0, completion_tokens=0):
    """构造 mock stream chunk"""
    chunk = MagicMock()
    chunk.content = content
    chunk.prompt_tokens = prompt_tokens
    chunk.completion_tokens = completion_tokens
    chunk.credits_consumed = None
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
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock), \
             patch.object(chat_handler, "_get_context_summary", new_callable=AsyncMock, return_value=None), \
             patch.object(chat_handler, "_update_summary_if_needed", new_callable=AsyncMock):
            mock_ws.send_to_task_or_user = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "今天天气怎么样"}],
                model_id="gemini-3-flash",
            )

        # 前 2 条是思考语言指令 + 当前时间注入
        assert captured[0] == {"role": "system", "content": "请使用中文进行思考和推理。"}
        assert captured[1]["role"] == "system"
        assert "当前时间" in captured[1]["content"]
        assert captured[2] == {"role": "system", "content": "你是AI助手"}
        assert captured[3] == {"role": "user", "content": _ts("你好")}
        assert captured[4] == {"role": "assistant", "content": _ts("你好！有什么可以帮你的？")}
        # 话题聚焦指令（紧贴用户消息前）
        assert captured[5]["role"] == "system"
        assert "最新问题" in captured[5]["content"]
        assert captured[6]["role"] == "user"
        assert captured[6]["content"] == "今天天气怎么样"

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
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock), \
             patch.object(chat_handler, "_get_context_summary", new_callable=AsyncMock, return_value=None), \
             patch.object(chat_handler, "_update_summary_if_needed", new_callable=AsyncMock):
            mock_ws.send_to_task_or_user = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "新问题"}],
                model_id="gemini-3-flash",
            )

        # 前 2 条是思考语言指令 + 当前时间注入
        assert captured[0] == {"role": "system", "content": "请使用中文进行思考和推理。"}
        assert captured[1]["role"] == "system"
        assert "当前时间" in captured[1]["content"]
        assert captured[2] == {"role": "user", "content": _ts("之前的问题")}
        assert captured[3] == {"role": "assistant", "content": _ts("之前的回答")}
        # 话题聚焦指令
        assert captured[4]["role"] == "system"
        assert "最新问题" in captured[4]["content"]
        assert captured[5]["role"] == "user"
        assert captured[5]["content"] == "新问题"

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
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock), \
             patch.object(chat_handler, "_get_context_summary", new_callable=AsyncMock, return_value=None), \
             patch.object(chat_handler, "_update_summary_if_needed", new_callable=AsyncMock):
            mock_ws.send_to_task_or_user = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[{"type": "text", "text": "第一条消息"}],
                model_id="gemini-3-flash",
            )

        # 前 2 条是思考语言指令 + 当前时间注入
        assert captured[0] == {"role": "system", "content": "请使用中文进行思考和推理。"}
        assert captured[1]["role"] == "system"
        assert "当前时间" in captured[1]["content"]
        assert captured[2]["role"] == "user"
        assert captured[2]["content"] == "第一条消息"

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
             patch.object(chat_handler, "_extract_memories_async", new_callable=AsyncMock), \
             patch.object(chat_handler, "_get_context_summary", new_callable=AsyncMock, return_value=None), \
             patch.object(chat_handler, "_update_summary_if_needed", new_callable=AsyncMock):
            mock_ws.send_to_task_or_user = AsyncMock()

            await chat_handler._stream_generate(
                task_id="t1", message_id="m1", conversation_id="conv1",
                user_id="u1",
                content=[
                    {"type": "text", "text": "这张图是什么"},
                    {"type": "image", "url": "https://img.png"},
                ],
                model_id="gemini-3-flash",
            )

        # 前 2 条是思考语言指令 + 当前时间注入
        assert captured[0] == {"role": "system", "content": "请使用中文进行思考和推理。"}
        assert captured[1]["role"] == "system"
        assert "当前时间" in captured[1]["content"]
        assert captured[2] == {"role": "user", "content": _ts("之前的对话")}
        assert captured[3] == {"role": "assistant", "content": _ts("之前的回复")}
        # 话题聚焦指令
        assert captured[4]["role"] == "system"
        assert "最新问题" in captured[4]["content"]
        assert captured[5]["role"] == "user"
        assert isinstance(captured[5]["content"], list)


# ============ Test _build_llm_messages gather exception degradation ============


class TestBuildLlmMessagesGatherDegradation:
    """asyncio.gather 中单个任务异常时降级（不影响其他结果）"""

    @pytest.mark.asyncio
    async def test_memory_exception_degrades_to_none(self, chat_handler, mock_db):
        """记忆检索异常 → 降级为 None，摘要和历史正常注入"""
        # Phase 6 门控：context_messages > 5 条时才注入摘要，所以需要足够多的历史
        mock_db.set_table_data("messages", [
            _make_msg("user", "问题1"),
            _make_msg("assistant", "回复1"),
            _make_msg("user", "问题2"),
            _make_msg("assistant", "回复2"),
            _make_msg("user", "问题3"),
            _make_msg("assistant", "回复3"),
        ])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            side_effect=RuntimeError("Mem0 timeout"),
        ), patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value="摘要内容",
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
            )

        # 摘要应被注入
        summaries = [m for m in messages if m["role"] == "system" and "摘要" in m.get("content", "")]
        assert len(summaries) == 1
        # 记忆不应被注入（无 memory 关键词的 system prompt）
        # 最后一条是用户消息
        assert messages[-1] == {"role": "user", "content": "你好"}

    @pytest.mark.asyncio
    async def test_summary_exception_degrades_to_none(self, chat_handler, mock_db):
        """摘要获取异常 → 降级为 None，记忆和历史正常"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value="你喜欢Python",
        ), patch.object(
            chat_handler, "_get_context_summary",
            side_effect=RuntimeError("DB down"),
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
            )

        # 记忆应被注入
        memory_msgs = [m for m in messages if m.get("content") == "你喜欢Python"]
        assert len(memory_msgs) == 1
        # 无摘要
        summary_msgs = [m for m in messages if "摘要" in m.get("content", "")]
        assert len(summary_msgs) == 0

    @pytest.mark.asyncio
    async def test_context_exception_degrades_to_empty(self, chat_handler, mock_db):
        """历史上下文异常 → 降级为空列表，记忆和摘要正常"""
        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler, "_build_context_messages",
            side_effect=RuntimeError("DB timeout"),
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
            )

        # 应只有基础 system prompts + 用户消息（无历史上下文、无话题聚焦指令）
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "你好"
        # 无话题聚焦（因为无历史上下文）
        focus_msgs = [m for m in messages if "最新问题" in m.get("content", "")]
        assert len(focus_msgs) == 0


# ============ Test prefetched_memory parameter ============


class TestBuildLlmMessagesPrefetchedMemory:
    """prefetched_memory 参数：有值时跳过 _build_memory_prompt"""

    @pytest.mark.asyncio
    async def test_prefetched_memory_skips_build(self, chat_handler, mock_db):
        """传入 prefetched_memory 时，不调用 _build_memory_prompt"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value="不应被调用",
        ) as mock_build, patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
                prefetched_memory="你喜欢Python编程",
            )

        # _build_memory_prompt 不应被调用
        mock_build.assert_not_called()
        # 预取的记忆应被注入
        memory_msgs = [m for m in messages if m.get("content") == "你喜欢Python编程"]
        assert len(memory_msgs) == 1

    @pytest.mark.asyncio
    async def test_no_prefetched_memory_calls_build(self, chat_handler, mock_db):
        """不传 prefetched_memory 时，正常调用 _build_memory_prompt"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value="记忆内容",
        ) as mock_build, patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
            )

        mock_build.assert_called_once()
        memory_msgs = [m for m in messages if m.get("content") == "记忆内容"]
        assert len(memory_msgs) == 1


# ============ user_location 注入测试 ============


class TestBuildLlmMessagesUserLocation:
    """user_location 参数注入系统提示词测试"""

    @pytest.mark.asyncio
    async def test_location_injected_when_provided(self, chat_handler, mock_db):
        """传入 user_location 时，注入位置系统消息"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "今天天气怎么样"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="今天天气怎么样",
                user_location="浙江省金华市",
            )

        location_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "用户所在位置：浙江省金华市" in m["content"]
        ]
        assert len(location_msgs) == 1
        assert location_msgs[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_no_location_when_none(self, chat_handler, mock_db):
        """user_location 为 None 时，不注入位置消息"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
                user_location=None,
            )

        location_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "用户所在位置" in m["content"]
        ]
        assert len(location_msgs) == 0

    @pytest.mark.asyncio
    async def test_location_not_injected_when_empty(self, chat_handler, mock_db):
        """user_location 为空字符串时，不注入位置消息"""
        mock_db.set_table_data("messages", [])

        with patch.object(
            chat_handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1",
                conversation_id="conv1",
                text_content="你好",
                user_location="",
            )

        location_msgs = [
            m for m in messages
            if isinstance(m.get("content"), str)
            and "用户所在位置" in m["content"]
        ]
        assert len(location_msgs) == 0


# ============ Test _should_skip_knowledge (Phase 6 门控) ============


class TestShouldSkipKnowledge:
    """Phase 6 反向门控：排除明确不需要知识库的场景"""

    @staticmethod
    def _skip(text: str) -> bool:
        from services.handlers.chat_context_mixin import ChatContextMixin
        return ChatContextMixin._should_skip_knowledge(text)

    def test_chitchat_skipped(self):
        """纯问候/闲聊跳过"""
        for text in ["你好", "早上好", "hi", "hello", "谢谢", "再见", "666"]:
            assert self._skip(text) is True, f"'{text}' should be skipped"

    def test_very_short_skipped(self):
        """极短消息（<=3字）跳过"""
        assert self._skip("嗯") is True
        assert self._skip("好") is True

    def test_creative_skipped(self):
        """创作/娱乐意图跳过"""
        assert self._skip("写一首关于春天的诗") is True
        assert self._skip("画一个猫") is True
        assert self._skip("讲个笑话") is True
        assert self._skip("今天天气怎么样") is True

    def test_general_qa_skipped(self):
        """通用问答跳过（不含业务词）"""
        assert self._skip("什么是REST API") is True
        assert self._skip("如何学习Python") is True

    def test_general_qa_with_business_not_skipped(self):
        """通用问答含业务词不跳过"""
        assert self._skip("什么是退货流程") is False
        assert self._skip("解释一下库存锁定") is False

    def test_business_queries_not_skipped(self):
        """业务查询不跳过"""
        for text in [
            "蓝色连衣裙卖了多少",
            "帮我查一下库存",
            "订单1234567890什么状态",
            "昨天的销量统计",
            "对比一下本周和上周",
        ]:
            assert self._skip(text) is False, f"'{text}' should NOT be skipped"

    def test_ambiguous_defaults_to_inject(self):
        """模糊指令默认注入"""
        assert self._skip("帮我看看那个") is False
        assert self._skip("查一下呗") is False

    def test_summary_request_not_skipped(self):
        """'帮我写个总结'不跳过（可能是 ERP 数据总结）"""
        assert self._skip("帮我写个总结") is False


# ============ Test _fetch_knowledge 两路并行召回 ============


class TestFetchKnowledgeParallel:
    """验证 _fetch_knowledge 两路并行召回 + _source tag。"""

    def _make_mixin(self):
        from services.handlers.chat_context_mixin import ChatContextMixin
        mixin = ChatContextMixin()
        mixin.org_id = "test_org"
        return mixin

    @pytest.mark.asyncio
    async def test_both_results_merged(self):
        """general + experience 结果合并返回"""
        general_items = [{"title": "知识1", "content": "内容1"}]
        exp_items = [{"title": "经验1", "content": "查询：xx\n路径：trade"}]

        async def mock_search(**kwargs):
            if kwargs.get("category") == "experience":
                return exp_items
            return general_items

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("各平台退货率")

        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_experience_has_source_tag(self):
        """experience 结果有 _source='experience' tag"""
        exp_items = [{"title": "经验1", "content": "内容"}]

        async def mock_search(**kwargs):
            if kwargs.get("category") == "experience":
                return exp_items
            return []

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("测试查询")

        tagged = [r for r in result if r.get("_source") == "experience"]
        assert len(tagged) == 1

    @pytest.mark.asyncio
    async def test_general_no_source_tag(self):
        """general 结果没有 _source tag"""
        general_items = [{"title": "知识1", "content": "内容"}]

        async def mock_search(**kwargs):
            if kwargs.get("category") == "experience":
                return []
            return general_items

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("测试查询")

        untagged = [r for r in result if r.get("_source") != "experience"]
        assert len(untagged) == 1

    @pytest.mark.asyncio
    async def test_experience_failure_isolated(self):
        """experience 召回失败不影响 general"""
        general_items = [{"title": "知识1", "content": "内容"}]

        call_count = 0

        async def mock_search(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("category") == "experience":
                raise ConnectionError("DB down")
            return general_items

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("测试查询")

        assert result is not None
        assert len(result) == 1
        assert result[0]["title"] == "知识1"

    @pytest.mark.asyncio
    async def test_general_failure_isolated(self):
        """general 召回失败不影响 experience"""
        exp_items = [{"title": "经验1", "content": "内容"}]

        async def mock_search(**kwargs):
            if kwargs.get("category") == "experience":
                return exp_items
            raise ConnectionError("DB down")

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("测试查询")

        assert result is not None
        assert len(result) == 1
        assert result[0].get("_source") == "experience"

    @pytest.mark.asyncio
    async def test_both_empty_returns_none(self):
        """两路都空返回 None"""
        async def mock_search(**kwargs):
            return []

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            result = await mixin._fetch_knowledge("测试查询")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_query_returns_none(self):
        """空 query 直接返回 None"""
        mixin = self._make_mixin()
        result = await mixin._fetch_knowledge("")
        assert result is None

    @pytest.mark.asyncio
    async def test_experience_search_params(self):
        """验证 experience 召回的搜索参数正确"""
        calls = []

        async def mock_search(**kwargs):
            calls.append(kwargs)
            return []

        mixin = self._make_mixin()
        with patch("services.knowledge_service.search_relevant", side_effect=mock_search):
            await mixin._fetch_knowledge("各平台退货率")

        assert len(calls) == 2
        exp_call = [c for c in calls if c.get("category") == "experience"][0]
        assert exp_call["node_type"] == "routing_pattern"
        assert exp_call["min_confidence"] == 0.6
        assert exp_call["org_id"] == "test_org"
        assert exp_call["limit"] == 2
