"""
WecomMessageService 单元测试

覆盖：handle_message Actor 分发、_reply_text 与 _push_stream_chunk 双通道。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from typing import Dict
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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
        org_id="org_test",
        corp_id="corp_test",
        agent_secret="secret_test",
    )


# ============================================================
# TestReplyText# ============================================================
# TestHandleMessage# ============================================================
# TestHandleMessage
# ============================================================


class TestHandleMessage:
    """handle_message 完整流程"""

    @pytest.mark.asyncio
    async def test_text_message_always_enqueues_actor(self):
        """文本消息统一进入 Actor，不再回退旧链路。"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()

        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg()
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._enqueue_actor_message.assert_awaited_once_with(
            msg, ctx, "uid1", "conv1", [],
        )

    @pytest.mark.asyncio
    async def test_actor_enqueue_does_not_create_legacy_messages(self):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg()
        ctx = _make_reply_ctx()
        await svc.handle_message(msg, ctx)

        svc._enqueue_actor_message.assert_awaited_once_with(
            msg, ctx, "uid1", "conv1", [],
        )

    @pytest.mark.asyncio
    async def test_actor_enqueue_checks_balance_and_acknowledges_new_task(self):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_user_balance = MagicMock(return_value=10)
        svc._notify_web_conversation_updated = AsyncMock()
        ctx = _make_reply_ctx()
        result = MagicMock(already_enqueued=False)

        with patch(
            "services.wecom.actor_enqueue.enqueue_wecom_message",
            new=AsyncMock(return_value=result),
        ) as mock_enqueue, patch(
            "services.handlers.get_handler",
            return_value=MagicMock(),
        ), patch(
            "services.wecom.stream_keepalive.register_stream_keepalive",
            return_value=True,
        ), patch(
            "services.wecom.actor_enqueue.stable_wecom_task_id",
            return_value="task-1",
        ):
            await svc._enqueue_actor_message(
                _make_msg(), ctx, "uid1", "conv1", [],
            )

        mock_enqueue.assert_awaited_once()
        assert ctx.active_stream_id is not None
        assert mock_enqueue.await_args.kwargs["stream_context"]["req_id"] == "req001"
        assert ctx.ws_client.send_stream_chunk.await_count == 1
        ctx.ws_client.send_stream_chunk.assert_any_await(
            req_id=ctx.req_id,
            stream_id=ANY,
            content="🤔 思考中…",
            finish=False,
            feedback_id=None,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chat_type", [WecomChatType.SINGLE, WecomChatType.GROUP])
    async def test_file_is_staged_without_starting_generation(self, chat_type):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_user_balance = MagicMock(return_value=10)
        svc._reply_text = AsyncMock()
        svc._notify_web_conversation_updated = AsyncMock()
        file_payload = {
            "url": "https://cdn/report.csv",
            "workspace_path": "上传/企微/stable_report.csv",
            "name": "report.csv",
            "mime_type": "text/csv",
            "size": 10,
        }
        svc._prepare_wecom_file = AsyncMock(return_value=file_payload)
        msg = _make_msg(msgtype=WecomMsgType.FILE, text="")
        msg.chattype = chat_type
        if chat_type == WecomChatType.GROUP:
            msg.chatid = "group-1"
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.attachment_service.stage_wecom_attachment",
            return_value=MagicMock(already_staged=False),
        ) as stage:
            await svc._stage_file_message(
                msg, ctx, "uid1", "conv1",
            )

        assert stage.call_args.kwargs["file_payload"] == file_payload
        expected_scope = "channel" if chat_type == WecomChatType.GROUP else "user"
        assert stage.call_args.kwargs["storage_scope"] == expected_scope
        owner = stage.call_args.kwargs["storage_owner_id"]
        assert owner.startswith("channels/wecom/") if expected_scope == "channel" else owner == "uid1"
        svc._reply_text.assert_awaited_once_with(
            ctx, "文件已收到，请告诉我需要如何处理。",
        )

    @pytest.mark.asyncio
    async def test_actor_replay_always_finishes_stream_acknowledgement(self):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._get_user_balance = MagicMock(return_value=10)
        svc._notify_web_conversation_updated = AsyncMock()
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.actor_enqueue.enqueue_wecom_message",
            new=AsyncMock(return_value=MagicMock(already_enqueued=True)),
        ), patch(
            "services.handlers.get_handler",
            return_value=MagicMock(),
        ):
            await svc._enqueue_actor_message(
                _make_msg(), ctx, "uid1", "conv1", [],
            )

        assert ctx.active_stream_id is None
        assert ctx.ws_client.send_stream_chunk.await_count == 2
        assert (
            ctx.ws_client.send_stream_chunk.await_args_list[-1].kwargs["content"]
            == "该消息已经收到，正在处理中。"
        )

    @pytest.mark.asyncio
    async def test_image_message_always_enqueues_actor(self):
        """图片消息统一进入 Actor 多模态链路。"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.IMAGE)
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._enqueue_actor_message.assert_awaited_once_with(
            msg, ctx, "uid1", "conv1", [],
        )

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
        svc._reply_text = AsyncMock()

        msg = _make_msg(msgtype="location")
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

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
        """语音消息 → 统一进入 Actor。"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.VOICE)
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._enqueue_actor_message.assert_awaited_once_with(
            msg, ctx, "uid1", "conv1", [],
        )


# ============================================================
# TestGetOrCreateConversation# ============================================================
# TestGetOrCreateConversation# ============================================================
# TestGetOrCreateConversation
# ============================================================


class TestGetOrCreateConversation:
    """_get_or_create_conversation 对话管理"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chat_type", ["single", WecomChatType.GROUP])
    async def test_resolves_provider_conversation(self, chat_type):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        db.rpc.return_value.execute.return_value = MagicMock(
            data={"conversation_id": "conv-existing"},
        )

        result = await svc._get_or_create_conversation(
            "u1", "chat1", chat_type, "corp1",
        )
        assert result == "conv-existing"
        db.rpc.assert_called_once_with("resolve_wecom_conversation", {
            "p_user_id": "u1",
            "p_corp_id": "corp1",
            "p_external_chat_id": "chat1",
            "p_chat_type": chat_type,
        })


# ============================================================
# TestCommandInterception# ============================================================
# TestCommandInterception — 指令拦截早期返回
# ============================================================


class TestCommandInterception:
    """handle_message 中指令匹配成功 → 不进入 AI 路由"""

    @pytest.mark.asyncio
    async def test_command_match_skips_ai(self):
        """文本“帮助”匹配指令后，不进入 Actor。"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()

        # mock _get_or_create_conversation
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg(text="帮助")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.command_handler.CommandHandler.try_handle",
            new=AsyncMock(return_value=True),
        ) as mock_try:
            await svc.handle_message(msg, ctx)

            mock_try.assert_called_once()
            svc._enqueue_actor_message.assert_not_awaited()

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
        svc._enqueue_actor_message = AsyncMock()

        msg = _make_msg(text="今天天气怎么样")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.command_handler.CommandHandler.try_handle",
            new=AsyncMock(return_value=False),
        ):
            await svc.handle_message(msg, ctx)
            svc._enqueue_actor_message.assert_awaited_once_with(
                msg, ctx, "uid1", "conv1", [],
            )


# ============================================================
# TestFileVideoHint — FILE Actor / VIDEO 不支持提示
# ============================================================


class TestFileVideoHint:
    """FILE → 持久化暂存，VIDEO → 提示不支持"""

    @pytest.mark.asyncio
    async def test_file_is_staged_without_legacy_messages(self):
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._stage_file_message = AsyncMock()

        msg = _make_msg(msgtype=WecomMsgType.FILE, text="")
        msg.file_url = "https://example.com/test.pdf"
        msg.file_name = "test.pdf"
        ctx = _make_reply_ctx()

        await svc.handle_message(msg, ctx)

        svc._stage_file_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_video_replies_hint(self):
        """VIDEO 消息 → 提示暂不支持"""
        db = _make_db_mock()
        svc = WecomMessageService(db)
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])

        msg = _make_msg(msgtype=WecomMsgType.VIDEO, text="")
        ctx = _make_reply_ctx()

        with patch.object(svc, "_reply_text", new=AsyncMock()) as mock_reply:
            await svc.handle_message(msg, ctx)
            mock_reply.assert_called_once()
            assert "不支持" in mock_reply.call_args[0][1]

# ============================================================
# TestHandleMessageNotify
# ============================================================


class TestHandleMessageNotify:
    """handle_message 流程中 _notify_web_conversation_updated 被调用"""

    @pytest.mark.asyncio
    async def test_notify_called_after_actor_enqueue(self):
        """Actor 入队后 → 调用 notify。"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._user_svc = MagicMock()
        svc._user_svc.get_or_create_user = AsyncMock(return_value="uid1")
        svc._user_svc.update_last_chatid = AsyncMock()
        svc._user_svc.upsert_chat_target = AsyncMock()
        svc._get_or_create_conversation = AsyncMock(return_value="conv1")
        svc._download_media = AsyncMock(return_value=[])
        svc._get_user_balance = MagicMock(return_value=10)

        msg = _make_msg()
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.actor_enqueue.enqueue_wecom_message",
            new=AsyncMock(return_value=MagicMock(already_enqueued=False)),
        ), patch(
            "services.handlers.get_handler",
            return_value=MagicMock(),
        ), patch.object(
            WecomMessageService,
            "_notify_web_conversation_updated",
            new=AsyncMock(),
        ) as mock_notify:
            await svc.handle_message(msg, ctx)
            mock_notify.assert_awaited_once_with("uid1", "conv1", org_id=None)
