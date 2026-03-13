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
    async def test_app_channel(self):
        """app 通道 → 调用 app_message_sender.send_text"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        ctx = _make_reply_ctx("app")

        with patch(
            "services.wecom.app_message_sender.send_text",
            new=AsyncMock(),
        ) as mock_send:
            await svc._reply_text(ctx, "hi from app")

        mock_send.assert_called_once_with(
            wecom_userid="user_abc",
            content="hi from app",
            agent_id=1000006,
        )

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
        ) as mock_send:
            # finish=False → 不发送
            await svc._push_stream_chunk(ctx, "s1", "partial", finish=False)
            mock_send.assert_not_called()

            # finish=True → 发送完整内容
            await svc._push_stream_chunk(ctx, "s1", "full text", finish=True)
            mock_send.assert_called_once_with(
                wecom_userid="user_abc",
                content="full text",
                agent_id=1000006,
            )


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
