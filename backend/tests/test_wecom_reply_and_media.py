"""企业微信回复通道与入站图片持久化测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.wecom import WecomIncomingMessage, WecomReplyContext
from services.wecom.stream_keepalive import (
    register_stream_keepalive,
    stop_stream_keepalive,
)
from services.wecom.wecom_message_service import WecomMessageService


def _service() -> WecomMessageService:
    return WecomMessageService(MagicMock())


def _robot_context() -> WecomReplyContext:
    return WecomReplyContext(
        channel="smart_robot",
        ws_client=AsyncMock(),
        req_id="req001",
    )


def _app_context() -> WecomReplyContext:
    return WecomReplyContext(
        channel="app",
        wecom_userid="user_abc",
        agent_id=1000006,
        org_id="org_test",
        corp_id="corp_test",
        agent_secret="secret_test",
    )


def test_balance_reads_user_credits() -> None:
    service = _service()
    query = service.db.table.return_value.select.return_value.eq.return_value
    query.single.return_value.execute.return_value.data = {"credits": 42}

    assert service._get_user_balance("user-1") == 42


def test_balance_defaults_to_zero_for_missing_user() -> None:
    service = _service()
    query = service.db.table.return_value.select.return_value.eq.return_value
    query.single.return_value.execute.return_value.data = None

    assert service._get_user_balance("missing") == 0


@pytest.mark.asyncio
async def test_stream_keepalive_registry_rejects_duplicate_and_stops_owner():
    first = MagicMock()
    first.stop = AsyncMock()
    duplicate = MagicMock()

    assert register_stream_keepalive("task-registry", first) is True
    assert register_stream_keepalive("task-registry", duplicate) is False

    await stop_stream_keepalive("task-registry")

    first.stop.assert_awaited_once()
    duplicate.stop.assert_not_called()


@pytest.mark.asyncio
async def test_robot_reply_uses_existing_stream() -> None:
    service = _service()
    context = _robot_context()
    context.active_stream_id = "stream-1"

    await service._reply_text(context, "完成")

    context.ws_client.send_stream_chunk.assert_awaited_once_with(
        req_id="req001",
        stream_id="stream-1",
        content="完成",
        finish=True,
    )
    assert context.active_stream_id is None


@pytest.mark.asyncio
async def test_robot_reply_without_stream_uses_reply() -> None:
    service = _service()
    context = _robot_context()

    await service._reply_text(context, "完成")

    context.ws_client.send_reply.assert_awaited_once_with(
        req_id="req001",
        msgtype="text",
        content={"content": "完成"},
    )


@pytest.mark.asyncio
async def test_app_stream_only_sends_finished_content() -> None:
    service = _service()
    context = _app_context()
    service._send_app_message = AsyncMock()

    await service._push_stream_chunk(
        context, "stream-1", "处理中", finish=False,
    )
    service._send_app_message.assert_not_awaited()

    await service._push_stream_chunk(
        context, "stream-1", "完成", finish=True,
    )
    service._send_app_message.assert_awaited_once_with(context, "完成")


@pytest.mark.asyncio
async def test_app_markdown_falls_back_to_text() -> None:
    service = _service()
    context = _app_context()

    with patch(
        "services.wecom.app_message_sender.send_markdown",
        new=AsyncMock(return_value=False),
    ) as send_markdown, patch(
        "services.wecom.app_message_sender.send_text",
        new=AsyncMock(),
    ) as send_text:
        await service._send_app_message(context, "# 标题")

    send_markdown.assert_awaited_once()
    send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_credit_error_uses_robot_card() -> None:
    service = _service()
    context = _robot_context()

    await service._reply_credits_insufficient(
        context, needed=100, balance=20, action="图片",
    )

    context.ws_client.send_template_card.assert_awaited_once()


@pytest.mark.asyncio
async def test_credit_error_uses_app_text() -> None:
    service = _service()
    context = _app_context()
    service._send_app_message = AsyncMock()

    await service._reply_credits_insufficient(
        context, needed=100, balance=20, action="图片",
    )

    text = service._send_app_message.await_args.args[1]
    assert "100" in text
    assert "20" in text


@pytest.mark.asyncio
async def test_image_download_keeps_only_successful_urls() -> None:
    service = _service()
    message = WecomIncomingMessage(
        msgid="msg-1",
        wecom_userid="user-1",
        corp_id="corp-1",
        chatid="user-1",
        chattype="single",
        msgtype="image",
        channel="smart_robot",
        image_urls=["https://one", "https://two"],
        aeskeys={"https://one": "key-1"},
    )

    with patch(
        "services.wecom.media_downloader.WecomMediaDownloader.download_and_store",
        new=AsyncMock(side_effect=["https://cdn/one", None]),
    ) as download:
        result = await service._download_media(message, "user-1")

    assert result == ["https://cdn/one"]
    assert download.await_count == 2


@pytest.mark.asyncio
async def test_web_notification_is_best_effort() -> None:
    with patch(
        "services.wecom.wecom_reply_mixin.ws_manager.send_to_user",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        await WecomMessageService._notify_web_conversation_updated(
            "user-1", "conversation-1", org_id="org-1",
        )
