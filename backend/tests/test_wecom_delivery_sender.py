"""WecomDeliverySender 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.wecom.delivery_sender import WecomDeliveryItem, WecomDeliverySender


def test_build_items_uses_stable_content_indexes_and_ignores_internal_blocks():
    sender = WecomDeliverySender(object(), MagicMock())
    items = sender.build_items(
        {"status": "completed"},
        {"content": [
            {"type": "thinking", "text": "内部推理"},
            {"type": "text", "text": "完成"},
            {"type": "image", "url": "https://cdn/a.png"},
            {"type": "chart", "option": {"series": []}},
            {"type": "tool_step", "output": "raw"},
        ]},
        {"transport": "smart_robot"},
    )

    assert [(item.key, item.kind) for item in items] == [
        ("text:1", "text"), ("image:2", "image"), ("chart:3", "chart"),
    ]


def test_build_items_creates_failed_result_without_message():
    sender = WecomDeliverySender(object(), MagicMock())

    items = sender.build_items(
        {"status": "failed", "error_message": "超时"},
        None,
        {"transport": "smart_robot"},
    )

    assert items == [WecomDeliveryItem("error:0", "text", "超时")]


def test_build_items_splits_app_text_into_independent_checkpoints():
    sender = WecomDeliverySender(object(), MagicMock())

    items = sender.build_items(
        {"status": "completed"},
        {"content": [{"type": "text", "text": "甲" * 2100}]},
        {"transport": "app"},
    )

    assert len(items) > 1
    assert items[0].key == "text:0:0"
    assert items[1].key == "text:0:1"


@pytest.mark.asyncio
async def test_smart_robot_requires_connected_client():
    client = MagicMock(is_connected=False)
    sender = WecomDeliverySender(object(), lambda _org_id: client)

    sent = await sender.send(
        {"transport": "smart_robot", "org_id": "org", "chatid": "chat"},
        WecomDeliveryItem("text:0", "text", "你好"),
    )

    assert sent is False


@pytest.mark.asyncio
async def test_smart_robot_sends_image_as_markdown():
    client = MagicMock(is_connected=True)
    client.send_proactive = AsyncMock(return_value=True)
    sender = WecomDeliverySender(object(), lambda _org_id: client)

    sent = await sender.send(
        {"transport": "smart_robot", "org_id": "org", "chatid": "chat"},
        WecomDeliveryItem("image:0", "image", "https://cdn/a.png"),
    )

    assert sent is True
    assert client.send_proactive.call_args.kwargs["content"] == {
        "content": "![图片](https://cdn/a.png)"
    }


@pytest.mark.asyncio
async def test_smart_robot_renders_uploads_and_sends_chart_image():
    client = MagicMock(is_connected=True)
    client.upload_media = AsyncMock(return_value="media-1")
    client.send_media_message = AsyncMock(return_value=True)
    renderer = MagicMock()
    renderer.render = AsyncMock(return_value=b"png")
    sender = WecomDeliverySender(
        object(), lambda _org_id: client, chart_renderer=renderer,
    )
    chart = {"type": "chart", "option": {"series": []}}

    sent = await sender.send(
        {"transport": "smart_robot", "org_id": "org", "chatid": "chat"},
        WecomDeliveryItem("chart:1", "chart", chart),
    )

    assert sent is True
    renderer.render.assert_awaited_once_with(chart)
    client.upload_media.assert_awaited_once_with(
        b"png", media_type="image", filename="chart.png",
    )
    client.send_media_message.assert_awaited_once_with(
        "chat", "image", "media-1",
    )


@pytest.mark.asyncio
async def test_smart_robot_detects_connection_lost_during_send():
    client = MagicMock(is_connected=True)

    async def fail_locally(**_kwargs):
        client.is_connected = False
        return True

    client.send_proactive = AsyncMock(side_effect=fail_locally)
    sender = WecomDeliverySender(object(), lambda _org_id: client)

    sent = await sender.send(
        {"transport": "smart_robot", "org_id": "org", "chatid": "chat"},
        WecomDeliveryItem("text:0", "text", "你好"),
    )

    assert sent is False


@pytest.mark.asyncio
async def test_app_missing_credentials_is_explicit_failure():
    sender = WecomDeliverySender(object(), MagicMock())
    sender._resolver.get = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="CREDENTIALS_MISSING"):
        await sender.send(
            {
                "transport": "app", "org_id": "org", "corp_id": "corp",
                "wecom_userid": "user",
            },
            WecomDeliveryItem("text:0", "text", "你好"),
        )


@pytest.mark.asyncio
async def test_app_text_uses_resolved_credentials():
    sender = WecomDeliverySender(object(), MagicMock())
    sender._resolver.get = AsyncMock(side_effect=["1001", "secret"])
    with patch(
        "services.wecom.app_message_sender.send_text",
        new=AsyncMock(return_value=True),
    ) as send_text:
        sent = await sender.send(
            {
                "transport": "app", "org_id": "org", "corp_id": "corp",
                "wecom_userid": "user",
            },
            WecomDeliveryItem("text:0", "text", "你好"),
        )

    assert sent is True
    assert send_text.call_args.args[2].agent_id == 1001


@pytest.mark.asyncio
async def test_app_chart_renders_uploads_and_sends_image():
    renderer = MagicMock()
    renderer.render = AsyncMock(return_value=b"png")
    sender = WecomDeliverySender(
        object(), MagicMock(), chart_renderer=renderer,
    )
    sender._resolver.get = AsyncMock(side_effect=["1001", "secret"])
    with patch(
        "services.wecom.app_message_sender.upload_temp_media_bytes",
        new=AsyncMock(return_value="media-1"),
    ) as upload, patch(
        "services.wecom.app_message_sender.send_image",
        new=AsyncMock(return_value=True),
    ) as send_image:
        sent = await sender.send(
            {
                "transport": "app", "org_id": "org", "corp_id": "corp",
                "wecom_userid": "user",
            },
            WecomDeliveryItem(
                "chart:1", "chart",
                {"type": "chart", "option": {"series": []}},
            ),
        )

    assert sent is True
    upload.assert_awaited_once()
    send_image.assert_awaited_once()
