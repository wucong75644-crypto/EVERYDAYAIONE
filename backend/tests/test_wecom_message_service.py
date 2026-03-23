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
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()

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
    async def test_image_message_calls_handle_text(self):
        """图片消息 → 走 _handle_text（多模态）"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._save_user_message = AsyncMock(return_value="umsg1")
        svc._create_assistant_placeholder = AsyncMock(return_value="amsg1")
        svc._handle_text = AsyncMock()
        svc._reply_text = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.IMAGE)
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._handle_text.assert_called_once()
        call_kwargs = svc._handle_text.call_args.kwargs
        assert call_kwargs["image_urls"] == []

    @pytest.mark.asyncio
    async def test_unsupported_msgtype_replies_hint(self):
        """不支持的消息类型 → 回复提示"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._save_user_message = AsyncMock(return_value="umsg1")
        svc._create_assistant_placeholder = AsyncMock(return_value="amsg1")
        svc._handle_text = AsyncMock()
        svc._reply_text = AsyncMock()

        msg = _make_msg(msgtype="location")
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._handle_text.assert_not_called()
        svc._reply_text.assert_called_once()
        reply_text = svc._reply_text.call_args[0][1]
        assert "文字" in reply_text or "图片" in reply_text

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
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
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

    @pytest.mark.asyncio
    async def test_save_user_message_with_images(self):
        """保存含图片的用户消息"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.insert.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "msg-u2"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        msg_id = await svc._save_user_message(
            "c1", "u1", "看这张图",
            image_urls=["https://oss.example.com/img1.jpg"],
        )
        assert msg_id == "msg-u2"

        insert_data = chain.insert.call_args[0][0]
        content = insert_data["content"]
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "看这张图"}
        assert content[1] == {"type": "image", "url": "https://oss.example.com/img1.jpg"}

    @pytest.mark.asyncio
    async def test_save_user_message_image_only(self):
        """只有图片无文本"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.insert.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "msg-u3"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        msg_id = await svc._save_user_message(
            "c1", "u1", "",
            image_urls=["https://oss.example.com/img1.jpg"],
        )
        assert msg_id == "msg-u3"

        insert_data = chain.insert.call_args[0][0]
        content = insert_data["content"]
        assert len(content) == 1
        assert content[0]["type"] == "image"

    @pytest.mark.asyncio
    async def test_save_user_message_no_content(self):
        """无文本无图片 → 兜底空文本"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        chain = MagicMock()
        chain.insert.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"id": "msg-u4"}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        msg_id = await svc._save_user_message("c1", "u1", "", image_urls=[])
        assert msg_id == "msg-u4"

        insert_data = chain.insert.call_args[0][0]
        content = insert_data["content"]
        assert content == [{"type": "text", "text": ""}]


# ============================================================
# TestDownloadMedia
# ============================================================


class TestDownloadMedia:
    """_download_media 多媒体下载"""

    @pytest.mark.asyncio
    async def test_no_images_returns_empty(self):
        """无图片 → 空列表"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        msg = _make_msg()

        result = await svc._download_media(msg, "u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_downloads_images_to_oss(self):
        """有图片 → 调用 downloader → 返回 OSS URL"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        msg = WecomIncomingMessage(
            msgid="msg002",
            wecom_userid="user_abc",
            corp_id="corp1",
            chatid="user_abc",
            chattype=WecomChatType.SINGLE,
            msgtype=WecomMsgType.IMAGE,
            channel="smart_robot",
            image_urls=["https://wecom.example.com/img1.jpg"],
            aeskeys={"https://wecom.example.com/img1.jpg": "aes123"},
        )

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_store",
            new=AsyncMock(return_value="https://oss.example.com/stored.jpg"),
        ):
            result = await svc._download_media(msg, "u1")

        assert result == ["https://oss.example.com/stored.jpg"]

    @pytest.mark.asyncio
    async def test_download_failure_skips(self):
        """下载失败 → 跳过该图片"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        msg = WecomIncomingMessage(
            msgid="msg003",
            wecom_userid="user_abc",
            corp_id="corp1",
            chatid="user_abc",
            chattype=WecomChatType.SINGLE,
            msgtype=WecomMsgType.IMAGE,
            channel="smart_robot",
            image_urls=["https://wecom.example.com/img_bad.jpg"],
        )

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_store",
            new=AsyncMock(return_value=None),
        ):
            result = await svc._download_media(msg, "u1")

        assert result == []


# ============================================================
# TestSessionSettings
# ============================================================


class TestSessionSettings:
    """会话级设置缓存"""

    def test_set_and_get(self):
        WecomMessageService.set_session_setting("c1", "model", "deepseek-v3.2")
        assert WecomMessageService.get_session_setting("c1", "model") == "deepseek-v3.2"

    def test_get_nonexistent(self):
        assert WecomMessageService.get_session_setting("nonexistent", "model") is None

    def test_overwrite(self):
        WecomMessageService.set_session_setting("c2", "model", "gpt-4")
        WecomMessageService.set_session_setting("c2", "model", "deepseek-r1")
        assert WecomMessageService.get_session_setting("c2", "model") == "deepseek-r1"


# ============================================================
# TestReplyTextWithStream — 有活跃 stream 时用 stream finish 替换
# ============================================================


class TestReplyTextWithStream:
    """_reply_text 在 active_stream_id 存在时走 stream finish 路径"""

    @pytest.mark.asyncio
    async def test_reply_text_uses_stream_finish(self):
        """有 active_stream_id → 用 send_stream_chunk finish 替换占位"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        ctx = _make_reply_ctx("smart_robot")
        ctx.active_stream_id = "stream_existing_123"

        await svc._reply_text(ctx, "AI 的回复内容")

        ctx.ws_client.send_stream_chunk.assert_called_once_with(
            req_id="req001",
            stream_id="stream_existing_123",
            content="AI 的回复内容",
            finish=True,
        )
        # stream 用完后应清空
        assert ctx.active_stream_id is None

    @pytest.mark.asyncio
    async def test_reply_text_no_stream_uses_send_reply(self):
        """无 active_stream_id → 用 send_reply 发送"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        ctx = _make_reply_ctx("smart_robot")
        assert ctx.active_stream_id is None

        await svc._reply_text(ctx, "直接回复")

        ctx.ws_client.send_reply.assert_called_once_with(
            req_id="req001",
            msgtype="text",
            content={"content": "直接回复"},
        )


# ============================================================
# TestReplyCreditsInsufficient — 积分不足回复
# ============================================================


class TestReplyCreditsInsufficient:
    """_reply_credits_insufficient 双通道回复"""

    @pytest.mark.asyncio
    async def test_smart_robot_sends_card(self):
        """智能机器人通道 → 发送积分不足卡片"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        ctx = _make_reply_ctx("smart_robot")

        await svc._reply_credits_insufficient(ctx, 100, 20, "图片")

        ctx.ws_client.send_template_card.assert_called_once()
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert "card_type" in card

    @pytest.mark.asyncio
    async def test_app_sends_text(self):
        """自建应用通道 → 发送文本"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        ctx = _make_reply_ctx("app")

        with patch.object(svc, "_send_app_message", new=AsyncMock()) as mock_send:
            await svc._reply_credits_insufficient(ctx, 100, 20, "图片")

            mock_send.assert_called_once()
            text_arg = mock_send.call_args[0][1]
            assert "100" in text_arg
            assert "20" in text_arg


# ============================================================
# TestCommandInterception — 指令拦截早期返回
# ============================================================


class TestCommandInterception:
    """handle_message 中指令匹配成功 → 不进入 AI 路由"""

    @pytest.mark.asyncio
    async def test_command_match_skips_ai(self):
        """文本"帮助"匹配指令 → try_handle=True → 不调用 _handle_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()

        # mock _get_or_create_conversation
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")

        msg = _make_msg(text="帮助")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.command_handler.CommandHandler.try_handle",
            new=AsyncMock(return_value=True),
        ) as mock_try:
            with patch.object(svc, "_handle_text", new=AsyncMock()) as mock_handle:
                await svc.handle_message(msg, ctx)

                mock_try.assert_called_once()
                mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_command_continues_to_ai(self):
        """普通文本 → try_handle=False → 正常路由到 AI"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._save_user_message = AsyncMock(return_value="m1")
        svc._create_assistant_placeholder = AsyncMock(return_value="a1")

        msg = _make_msg(text="今天天气怎么样")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.command_handler.CommandHandler.try_handle",
            new=AsyncMock(return_value=False),
        ):
            with patch.object(svc, "_handle_text", new=AsyncMock()) as mock_handle:
                await svc.handle_message(msg, ctx)
                mock_handle.assert_called_once()


# ============================================================
# TestFileVideoHint — FILE/VIDEO 不支持提示
# ============================================================


class TestFileVideoHint:
    """FILE/VIDEO msgtype → 提示不支持"""

    @pytest.mark.asyncio
    async def test_file_replies_hint(self):
        """FILE 消息 → 提示暂不支持"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._save_user_message = AsyncMock(return_value="m1")
        svc._create_assistant_placeholder = AsyncMock(return_value="a1")

        msg = _make_msg(msgtype=WecomMsgType.FILE, text="")
        ctx = _make_reply_ctx()

        with patch.object(svc, "_reply_text", new=AsyncMock()) as mock_reply:
            await svc.handle_message(msg, ctx)
            mock_reply.assert_called_once()
            assert "暂不支持" in mock_reply.call_args[0][1]


# ============================================================
# TestNotifyWebConversationUpdated
# ============================================================


class TestNotifyWebConversationUpdated:
    """_notify_web_conversation_updated WS 推送"""

    @pytest.mark.asyncio
    async def test_sends_to_user_via_ws_manager(self):
        """正常调用 → ws_manager.send_to_user 被调用"""
        with patch(
            "services.wecom.wecom_message_service.ws_manager"
        ) as mock_ws:
            mock_ws.send_to_user = AsyncMock()
            await WecomMessageService._notify_web_conversation_updated(
                "uid1", "conv1",
            )
            mock_ws.send_to_user.assert_awaited_once_with(
                "uid1",
                {"type": "conversation_updated", "conversation_id": "conv1"},
            )

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        """ws_manager 异常 → 不抛出，仅 warning"""
        with patch(
            "services.wecom.wecom_message_service.ws_manager"
        ) as mock_ws:
            mock_ws.send_to_user = AsyncMock(
                side_effect=ConnectionError("redis down")
            )
            # 不应抛出异常
            await WecomMessageService._notify_web_conversation_updated(
                "uid1", "conv1",
            )


class TestHandleMessageNotify:
    """handle_message 流程中 _notify_web_conversation_updated 被调用"""

    @pytest.mark.asyncio
    async def test_notify_called_after_save_user_message(self):
        """保存用户消息后 → 调用 notify"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._save_user_message = AsyncMock(return_value="m1")
        svc._create_assistant_placeholder = AsyncMock(return_value="a1")
        svc._handle_text = AsyncMock()

        msg = _make_msg()
        ctx = _make_reply_ctx()

        with patch.object(
            WecomMessageService,
            "_notify_web_conversation_updated",
            new=AsyncMock(),
        ) as mock_notify:
            await svc.handle_message(msg, ctx)
            mock_notify.assert_awaited_once_with("uid1", "conv1")
