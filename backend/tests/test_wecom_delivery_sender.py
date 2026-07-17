"""WecomDeliverySender 单元测试。"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.wecom.delivery_sender import WecomDeliveryItem, WecomDeliverySender


def test_build_items_keeps_stable_indexes_and_skips_charts():
    sender = WecomDeliverySender(object(), MagicMock())
    items = sender.build_items(
        {"id": "task-1", "status": "completed"},
        {"content": [
            {"type": "thinking", "text": "内部推理"},
            {"type": "text", "text": "完成"},
            {"type": "image", "url": "https://cdn/a.png"},
            {
                "type": "chart",
                "spec_format": "plotly",
                "option": {"data": []},
            },
            {"type": "video", "url": "https://cdn/a.mp4"},
            {"type": "tool_step", "output": "raw"},
        ]},
        {"transport": "smart_robot"},
    )

    assert [(item.key, item.kind) for item in items] == [
        ("text:1", "text"), ("image:2", "image"), ("video:4", "video"),
    ]


def test_build_items_combines_stream_text_into_one_checkpoint():
    sender = WecomDeliverySender(object(), MagicMock())

    items = sender.build_items(
        {"id": "task-1", "status": "completed"},
        {"content": [
            {"type": "text", "text": "第一段"},
            {"type": "chart", "spec_format": "plotly", "option": {}},
            {"type": "text", "text": "第二段"},
            {"type": "image", "url": "https://cdn/a.png"},
        ]},
        {"transport": "smart_robot", "stream_id": "stream-1"},
    )

    assert items == [
        WecomDeliveryItem("stream:text", "text", "第一段\n\n第二段"),
        WecomDeliveryItem("image:3", "image", "https://cdn/a.png"),
    ]


def test_build_items_finishes_stream_when_result_has_no_text():
    sender = WecomDeliverySender(object(), MagicMock())

    items = sender.build_items(
        {"id": "task-1", "status": "completed"},
        {"content": [{
            "type": "chart",
            "spec_format": "plotly",
            "option": {},
        }]},
        {"transport": "smart_robot", "stream_id": "stream-1"},
    )

    assert items == [
        WecomDeliveryItem("stream:text", "text", "分析已完成。"),
    ]


@pytest.mark.parametrize("spec_format", ["echarts", "plotly", "vegalite"])
def test_build_items_chart_only_completes_without_false_empty_reply(spec_format):
    sender = WecomDeliverySender(object(), MagicMock())

    items = sender.build_items(
        {"id": "task-1", "status": "completed"},
        {"content": [{
            "type": "chart",
            "spec_format": spec_format,
            "option": {"series": []},
        }]},
        {"transport": "smart_robot"},
    )

    assert items == []


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
async def test_smart_robot_finishes_current_stream_in_original_message():
    client = MagicMock(is_connected=True)
    client.send_stream_chunk = AsyncMock()
    sender = WecomDeliverySender(object(), lambda _org_id: client)
    context = {
        "transport": "smart_robot",
        "org_id": "org",
        "chatid": "chat",
        "stream_task_id": "task-1",
        "stream_req_id": "req-1",
        "stream_id": "stream-1",
        "stream_started_at": time.time(),
    }

    with patch(
        "services.wecom.delivery_sender.stop_stream_keepalive",
        new=AsyncMock(),
    ) as stop:
        sent = await sender.send(
            context,
            WecomDeliveryItem("stream:text", "text", "**完成**"),
        )

    assert sent is True
    stop.assert_awaited_once_with("task-1")
    client.send_stream_chunk.assert_awaited_once_with(
        req_id="req-1",
        stream_id="stream-1",
        content="**完成**",
        finish=True,
    )
    client.send_proactive.assert_not_called()


@pytest.mark.asyncio
async def test_smart_robot_falls_back_when_stream_expired():
    client = MagicMock(is_connected=True)
    client.send_proactive = AsyncMock(return_value=True)
    sender = WecomDeliverySender(object(), lambda _org_id: client)

    with patch(
        "services.wecom.delivery_sender.stop_stream_keepalive",
        new=AsyncMock(),
    ) as stop:
        sent = await sender.send(
            {
                "transport": "smart_robot",
                "org_id": "org",
                "chatid": "chat",
                "stream_task_id": "task-1",
                "stream_req_id": "req-1",
                "stream_id": "stream-1",
                "stream_started_at": 0,
            },
            WecomDeliveryItem("stream:text", "text", "完成"),
        )

    assert sent is True
    stop.assert_awaited_once_with("task-1")
    client.send_stream_chunk.assert_not_called()
    client.send_proactive.assert_awaited_once()


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
async def test_smart_robot_sends_video_as_text_link():
    client = MagicMock(is_connected=True)
    client.send_proactive = AsyncMock(return_value=True)
    sender = WecomDeliverySender(object(), lambda _org_id: client)

    sent = await sender.send(
        {"transport": "smart_robot", "org_id": "org", "chatid": "chat"},
        WecomDeliveryItem("video:0", "video", "https://cdn/a.mp4"),
    )

    assert sent is True
    assert client.send_proactive.call_args.kwargs == {
        "chatid": "chat",
        "msgtype": "text",
        "content": {"content": "视频已生成：https://cdn/a.mp4"},
    }


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
@pytest.mark.parametrize(
    ("kind", "url", "send_function"),
    [
        ("image", "https://cdn/a.png", "send_image"),
        ("video", "https://cdn/a.mp4", "send_video"),
    ],
)
async def test_app_uploads_and_sends_existing_media(kind, url, send_function):
    sender = WecomDeliverySender(object(), MagicMock())
    sender._resolver.get = AsyncMock(side_effect=["1001", "secret"])
    with patch(
        "services.wecom.app_message_sender.upload_temp_media",
        new=AsyncMock(return_value="media-1"),
    ) as upload, patch(
        f"services.wecom.app_message_sender.{send_function}",
        new=AsyncMock(return_value=True),
    ) as send_media:
        sent = await sender.send(
            {
                "transport": "app", "org_id": "org", "corp_id": "corp",
                "wecom_userid": "user",
            },
            WecomDeliveryItem(f"{kind}:0", kind, url),
        )

    assert sent is True
    upload.assert_awaited_once()
    send_media.assert_awaited_once()
