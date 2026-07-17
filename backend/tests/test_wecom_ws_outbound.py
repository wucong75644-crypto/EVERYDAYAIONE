import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from schemas.wecom import WecomCommand
from services.wecom.ws_client import WecomWSClient


@pytest.mark.asyncio
async def test_upload_media_uses_official_three_phase_protocol():
    client = WecomWSClient("bot", "secret")
    client._is_connected = True
    client._request = AsyncMock(side_effect=[
        {"body": {"upload_id": "upload-1"}},
        {"body": {}},
        {"body": {"media_id": "media-1"}},
    ])

    media_id = await client.upload_media(
        b"png-data", media_type="image", filename="chart.png",
    )

    assert media_id == "media-1"
    assert [call.args[0] for call in client._request.await_args_list] == [
        WecomCommand.UPLOAD_MEDIA_INIT,
        WecomCommand.UPLOAD_MEDIA_CHUNK,
        WecomCommand.UPLOAD_MEDIA_FINISH,
    ]


@pytest.mark.asyncio
async def test_request_correlates_ack_by_request_id():
    client = WecomWSClient("bot", "secret")
    client._is_connected = True
    client._ws = AsyncMock()

    request = asyncio.create_task(client._request("command", {"value": 1}))
    await asyncio.sleep(0)
    sent = json.loads(client._ws.send.await_args.args[0])
    future = client._pending_requests[sent["headers"]["req_id"]]
    future.set_result({
        "headers": sent["headers"], "errcode": 0, "body": {"ok": True},
    })

    assert (await request)["body"] == {"ok": True}


@pytest.mark.asyncio
async def test_upload_media_rejects_empty_payload():
    client = WecomWSClient("bot", "secret")
    with pytest.raises(ValueError, match="SIZE_INVALID"):
        await client.upload_media(
            b"", media_type="image", filename="chart.png",
        )


@pytest.mark.asyncio
async def test_upload_media_rejects_missing_upload_id():
    client = WecomWSClient("bot", "secret")
    client._request = AsyncMock(return_value={"body": {}})

    with pytest.raises(RuntimeError, match="INIT_INVALID"):
        await client.upload_media(
            b"png", media_type="image", filename="chart.png",
        )


@pytest.mark.asyncio
async def test_send_media_message_waits_for_ack():
    client = WecomWSClient("bot", "secret")
    client._is_connected = True
    client._request = AsyncMock(return_value={"body": {}})

    assert await client.send_media_message("chat", "image", "media") is True
    client._request.assert_awaited_once_with(
        WecomCommand.SEND_MSG,
        {
            "chatid": "chat", "msgtype": "image",
            "image": {"media_id": "media"},
        },
    )


@pytest.mark.asyncio
async def test_request_rejects_error_ack():
    client = WecomWSClient("bot", "secret")
    client._is_connected = True
    client._ws = AsyncMock()

    request = asyncio.create_task(client._request("command", {}))
    await asyncio.sleep(0)
    sent = json.loads(client._ws.send.await_args.args[0])
    client._pending_requests[sent["headers"]["req_id"]].set_result({
        "headers": sent["headers"], "errcode": 40001,
    })

    with pytest.raises(RuntimeError, match="WECOM_REQUEST_FAILED"):
        await request
