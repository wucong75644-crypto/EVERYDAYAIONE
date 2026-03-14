"""
WecomMessageService 单元测试

覆盖：handle_message 流程分发、_reply_text 双通道、
      _push_stream_chunk 双通道、_extract_text_from_content 解析
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.wecom.wecom_message_service import WecomMessageService


def _make_db_mock():
    """按表名隔离的 DB mock"""
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock()))
    db._table_mocks = table_mocks
    return db


def _make_msg(
    msgtype: str = WecomMsgType.TEXT,
    text: str = "你好",
    channel: str = "smart_robot",
) -> WecomIncomingMessage:
    return WecomIncomingMessage(
        msgid="msg001",
        wecom_userid="user_abc",
        corp_id="corp1",
        chatid="user_abc",
        chattype=WecomChatType.SINGLE,
        msgtype=msgtype,
        channel=channel,
        text_content=text,
    )


def _make_reply_ctx(channel: str = "smart_robot") -> WecomReplyContext:
    if channel == "smart_robot":
        return WecomReplyContext(
            channel="smart_robot",
            ws_client=AsyncMock(),
            req_id="req001",
        )
    return WecomReplyContext(
        channel="app",
        wecom_userid="user_abc",
        agent_id=1000006,
    )


# ============================================================
# TestExtractTextFromContent
# ============================================================


class TestExtractTextFromContent:
    """_extract_text_from_content 文本提取"""

    def test_string_input(self):
        assert WecomMessageService._extract_text_from_content("hello") == "hello"

    def test_content_parts_list(self):
        parts = [{"type": "text", "text": "你好世界"}]
        assert WecomMessageService._extract_text_from_content(parts) == "你好世界"

    def test_mixed_parts(self):
        parts = [
            {"type": "image", "url": "http://..."},
            {"type": "text", "text": "描述"},
        ]
        assert WecomMessageService._extract_text_from_content(parts) == "描述"

    def test_empty_list(self):
        assert WecomMessageService._extract_text_from_content([]) is None

    def test_none_input(self):
        assert WecomMessageService._extract_text_from_content(None) is None

    def test_dict_without_text_type(self):
        parts = [{"type": "image", "url": "http://..."}]
        assert WecomMessageService._extract_text_from_content(parts) is None

    def test_empty_string(self):
        assert WecomMessageService._extract_text_from_content("") == ""


# ============================================================
# TestReplyText
# ============================================================


class TestReplyText:
    """_reply_text 双通道回复"""

    @pytest.mark.asyncio
    async def test_smart_robot_channel(self):
        """smart_robot 通道 → 调用 ws_client.send_reply"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("smart_robot")

        await svc._reply_text(ctx, "hello")

        ctx.ws_client.send_reply.assert_called_once_with(
            req_id="req001",
            msgtype="text",
            content={"content": "hello"},
        )

    @pytest.mark.asyncio
    async def test_app_channel_plain_text(self):
        """app 通道纯文本 → 调用 send_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_send, patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ):
            await svc._reply_text(ctx, "hi from app")

        mock_send.assert_called_once_with(
            wecom_userid="user_abc",
            content="hi from app",
            agent_id=1000006,
        )

    @pytest.mark.asyncio
    async def test_app_channel_markdown(self):
        """app 通道含 Markdown → 调用 send_markdown"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ), patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ) as mock_md:
            await svc._reply_text(ctx, "# 标题\n\n**加粗**内容")

        mock_md.assert_called_once()
        call_kwargs = mock_md.call_args.kwargs
        assert call_kwargs["wecom_userid"] == "user_abc"
        assert "# 标题" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_unknown_channel_no_op(self):
        """未知通道 → 不发送"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = WecomReplyContext(channel="unknown")
        # 不应抛出异常
        await svc._reply_text(ctx, "test")


# ============================================================
# TestPushStreamChunk
# ============================================================


class TestPushStreamChunk:
    """_push_stream_chunk 流式推送"""

    @pytest.mark.asyncio
    async def test_smart_robot_sends_every_chunk(self):
        """smart_robot 通道 → 每次都调用 ws_client"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("smart_robot")

        await svc._push_stream_chunk(ctx, "s1", "partial", finish=False)
        ctx.ws_client.send_stream_chunk.assert_called_once()

    @pytest.mark.asyncio
    async def test_app_only_sends_on_finish(self):
        """app 通道 → 仅 finish=True 时发送"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_send, patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ):
            # finish=False → 不发送
            await svc._push_stream_chunk(ctx, "s1", "partial", finish=False)
            mock_send.assert_not_called()

            # finish=True → 发送完整内容（纯文本走 send_text）
            await svc._push_stream_chunk(ctx, "s1", "full text", finish=True)
            mock_send.assert_called_once_with(
                wecom_userid="user_abc",
                content="full text",
                agent_id=1000006,
            )

    @pytest.mark.asyncio
    async def test_app_finish_with_markdown_sends_markdown(self):
        """app 通道 finish=True 含 Markdown → send_markdown"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ), patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ) as mock_md:
            await svc._push_stream_chunk(
                ctx, "s1", "# 标题\n\n代码：\n```py\nprint(1)\n```",
                finish=True,
            )
            mock_md.assert_called_once()


# ============================================================
# TestHandleMessage
# ============================================================


class TestHandleMessage:
    """handle_message 完整流程"""

    @pytest.mark.asyncio
    async def test_text_message_calls_handle_text(self):
        """文本消息 → 调用 _handle_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")

        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._save_user_message = AsyncMock(return_value="umsg1")
        svc._create_assistant_placeholder = AsyncMock(return_value="amsg1")
        svc._handle_text = AsyncMock()
        svc._reply_text = AsyncMock()

        msg = _make_msg()
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._handle_text.assert_called_once()
        call_kwargs = svc._handle_text.call_args.kwargs
        assert call_kwargs["user_id"] == "uid1"
        assert call_kwargs["conversation_id"] == "conv1"
        assert call_kwargs["text_content"] == "你好"

    @pytest.mark.asyncio
    async def test_unsupported_msgtype_replies_hint(self):
        """不支持的消息类型 → 回复提示"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._save_user_message = AsyncMock(return_value="umsg1")
        svc._create_assistant_placeholder = AsyncMock(return_value="amsg1")
        svc._handle_text = AsyncMock()
        svc._reply_text = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.IMAGE)
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._handle_text.assert_not_called()
        svc._reply_text.assert_called_once()
        reply_text = svc._reply_text.call_args[0][1]
        assert "文字" in reply_text

    @pytest.mark.asyncio
    async def test_exception_sends_error_reply(self):
        """处理异常 → 发送错误回复"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(
            side_effect=RuntimeError("DB crash")
        )
        svc._reply_text = AsyncMock()

        msg = _make_msg()
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._reply_text.assert_called_once()
        reply_text = svc._reply_text.call_args[0][1]
        assert "问题" in reply_text or "稍后" in reply_text

    @pytest.mark.asyncio
    async def test_voice_message_treated_as_text(self):
        """语音消息 → 走文本处理流程"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._save_user_message = AsyncMock(return_value="umsg1")
        svc._create_assistant_placeholder = AsyncMock(return_value="amsg1")
        svc._handle_text = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.VOICE)
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._handle_text.assert_called_once()


# ============================================================
# TestSendAppMessage
# ============================================================


class TestSendAppMessage:
    """_send_app_message 格式适配 + 长消息分割"""

    @pytest.mark.asyncio
    async def test_plain_text_sends_text(self):
        """纯文本 → send_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_send, patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ):
            await svc._send_app_message(ctx, "普通文本消息")

        mock_send.assert_called_once_with(
            wecom_userid="user_abc",
            content="普通文本消息",
            agent_id=1000006,
        )

    @pytest.mark.asyncio
    async def test_markdown_sends_markdown(self):
        """含 Markdown → send_markdown"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ), patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ) as mock_md:
            await svc._send_app_message(ctx, "# 标题\n\n**内容**")

        mock_md.assert_called_once()
        call_kwargs = mock_md.call_args.kwargs
        assert call_kwargs["wecom_userid"] == "user_abc"

    @pytest.mark.asyncio
    async def test_markdown_fallback_to_text(self):
        """send_markdown 失败 → 降级 send_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_text, patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(return_value=False),
        ) as mock_md:
            await svc._send_app_message(ctx, "# 标题\n\n**内容**")

        mock_md.assert_called_once()
        mock_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_message_splits(self):
        """超长消息 → 分割为多条"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        # 构造超过 2000 字节的纯文本
        long_text = "A" * 3000

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_send, patch(
            "services.wecom.app_message_sender.send_markdown",
            new=AsyncMock(),
        ):
            await svc._send_app_message(ctx, long_text)

        assert mock_send.call_count >= 2


# ============================================================
# TestHandleText
# ============================================================


class TestHandleText:
    """_handle_text Agent Loop 路由 + 分发"""

    @pytest.mark.asyncio
    async def test_routes_to_chat(self):
        """Agent Loop 返回 CHAT → 调用 _handle_chat_response"""
        from schemas.message import GenerationType
        from services.agent_types import AgentResult

        db = _make_db_mock()
        svc = WecomMessageService(db)

        agent_result = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=1, total_tokens=100,
        )
        svc._run_agent_loop = AsyncMock(return_value=agent_result)
        svc._build_memory_prompt = AsyncMock(return_value="记忆")
        svc._handle_chat_response = AsyncMock()
        svc._handle_image_response = AsyncMock()
        svc._handle_video_response = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_text("u1", "c1", "m1", "你好", ctx)

        svc._handle_chat_response.assert_called_once()
        svc._handle_image_response.assert_not_called()
        svc._handle_video_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_to_image(self):
        """Agent Loop 返回 IMAGE → 调用 _handle_image_response"""
        from schemas.message import GenerationType
        from services.agent_types import AgentResult

        db = _make_db_mock()
        svc = WecomMessageService(db)

        agent_result = AgentResult(
            generation_type=GenerationType.IMAGE,
            turns_used=1, total_tokens=100,
        )
        svc._run_agent_loop = AsyncMock(return_value=agent_result)
        svc._build_memory_prompt = AsyncMock(return_value=None)
        svc._handle_chat_response = AsyncMock()
        svc._handle_image_response = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_text("u1", "c1", "m1", "画猫", ctx)

        svc._handle_image_response.assert_called_once()
        svc._handle_chat_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_to_video(self):
        """Agent Loop 返回 VIDEO → 调用 _handle_video_response"""
        from schemas.message import GenerationType
        from services.agent_types import AgentResult

        db = _make_db_mock()
        svc = WecomMessageService(db)

        agent_result = AgentResult(
            generation_type=GenerationType.VIDEO,
            turns_used=1, total_tokens=100,
        )
        svc._run_agent_loop = AsyncMock(return_value=agent_result)
        svc._build_memory_prompt = AsyncMock(return_value=None)
        svc._handle_video_response = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_text("u1", "c1", "m1", "生成视频", ctx)

        svc._handle_video_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_loop_failure_fallback(self):
        """Agent Loop 异常 → 调用 _handle_chat_fallback"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))
        svc._build_memory_prompt = AsyncMock(return_value=None)
        svc._handle_chat_fallback = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_text("u1", "c1", "m1", "你好", ctx)

        svc._handle_chat_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_memory_failure_still_routes(self):
        """记忆失败不影响路由"""
        from schemas.message import GenerationType
        from services.agent_types import AgentResult

        db = _make_db_mock()
        svc = WecomMessageService(db)

        agent_result = AgentResult(
            generation_type=GenerationType.CHAT,
            turns_used=1, total_tokens=100,
        )
        svc._run_agent_loop = AsyncMock(return_value=agent_result)
        svc._build_memory_prompt = AsyncMock(
            side_effect=RuntimeError("mem fail"),
        )
        svc._handle_chat_response = AsyncMock()

        ctx = _make_reply_ctx("smart_robot")
        await svc._handle_text("u1", "c1", "m1", "你好", ctx)

        svc._handle_chat_response.assert_called_once()
        # memory_prompt 应为 None
        call_args = svc._handle_chat_response.call_args
        assert call_args[0][6] is None  # 第 7 个位置参数 = memory_prompt


# ============================================================
# TestGetOrCreateConversation
# ============================================================


class TestGetOrCreateConversation:
    """_get_or_create_conversation 对话管理"""

    @pytest.mark.asyncio
    async def test_existing_conversation(self):
        """已有对话 → 返回 id"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.like.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "conv-existing"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        result = await svc._get_or_create_conversation("u1", "chat1", "single")
        assert result == "conv-existing"

    @pytest.mark.asyncio
    async def test_create_new_single(self):
        """无对话 → 创建单聊"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.like.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = []
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        svc._conv_svc = MagicMock()
        svc._conv_svc.create_conversation = AsyncMock(
            return_value={"id": "conv-new"},
        )

        result = await svc._get_or_create_conversation("u1", "chat1", "single")
        assert result == "conv-new"

        create_call = svc._conv_svc.create_conversation.call_args
        assert create_call.kwargs["title"] == "企微对话"

    @pytest.mark.asyncio
    async def test_create_new_group(self):
        """无对话 + 群聊 → 创建群聊对话"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.like.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = []
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        svc._conv_svc = MagicMock()
        svc._conv_svc.create_conversation = AsyncMock(
            return_value={"id": "conv-group"},
        )

        result = await svc._get_or_create_conversation(
            "u1", "chat1", WecomChatType.GROUP,
        )
        assert result == "conv-group"

        create_call = svc._conv_svc.create_conversation.call_args
        assert create_call.kwargs["title"] == "企微群聊"


# ============================================================
# TestGetConversationHistory
# ============================================================


class TestGetConversationHistory:
    """_get_conversation_history 历史消息"""

    @pytest.mark.asyncio
    async def test_returns_messages(self):
        """有历史 → 返回 role+content 列表"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.neq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [
            {"role": "assistant", "content": [{"type": "text", "text": "回答"}]},
            {"role": "user", "content": [{"type": "text", "text": "问题"}]},
        ]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        messages = await svc._get_conversation_history("c1", limit=10)

        # reversed: user先，assistant后
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_empty_history(self):
        """无历史 → 空列表"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.neq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = []
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        messages = await svc._get_conversation_history("c1")
        assert messages == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """异常 → 空列表"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        db.table = MagicMock(side_effect=RuntimeError("DB down"))

        messages = await svc._get_conversation_history("c1")
        assert messages == []


# ============================================================
# TestMessagePersistence
# ============================================================


class TestMessagePersistence:
    """_save_user_message / _create_assistant_placeholder / _update_assistant_message"""

    @pytest.mark.asyncio
    async def test_save_user_message(self):
        """保存用户消息 + 递增计数"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.insert.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "msg-u1"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        msg_id = await svc._save_user_message("c1", "u1", "你好")

        assert msg_id == "msg-u1"
        # 验证 insert 的内容
        insert_data = chain.insert.call_args[0][0]
        assert insert_data["role"] == "user"
        assert insert_data["status"] == "completed"
        # 验证 rpc 递增
        db.rpc.assert_called_once_with(
            "increment_message_count", {"conv_id": "c1"},
        )

    @pytest.mark.asyncio
    async def test_create_assistant_placeholder(self):
        """创建 assistant 占位"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.insert.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "msg-a1"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        msg_id = await svc._create_assistant_placeholder("c1")

        assert msg_id == "msg-a1"
        insert_data = chain.insert.call_args[0][0]
        assert insert_data["role"] == "assistant"
        assert insert_data["status"] == "generating"

    @pytest.mark.asyncio
    async def test_update_assistant_message(self):
        """更新 assistant 消息"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        await svc._update_assistant_message("m1", "回复内容")

        update_data = chain.update.call_args[0][0]
        assert update_data["status"] == "completed"
        assert update_data["content"][0]["text"] == "回复内容"
        chain.eq.assert_called_with("id", "m1")
