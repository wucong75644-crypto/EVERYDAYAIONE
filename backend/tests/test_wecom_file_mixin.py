"""企微 FILE 原始资产进入 Conversation Actor 的测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from schemas.wecom import WecomIncomingMessage, WecomMsgType, WecomReplyContext
from services.wecom.wecom_message_service import WecomMessageService


def _message(filename: str = "report.csv") -> WecomIncomingMessage:
    return WecomIncomingMessage(
        msgid="file-msg-1",
        wecom_userid="wx-user",
        corp_id="corp",
        chatid="chat",
        chattype="single",
        msgtype=WecomMsgType.FILE,
        channel="smart_robot",
        org_id="org",
        file_url="https://wecom.example/file",
        file_name=filename,
    )


def _context() -> WecomReplyContext:
    return WecomReplyContext(
        channel="smart_robot",
        ws_client=MagicMock(),
        req_id="req",
    )


def _service() -> WecomMessageService:
    service = WecomMessageService(MagicMock())
    service._reply_text = AsyncMock()
    return service


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(file_workspace_root=str(tmp_path))


@pytest.mark.asyncio
async def test_wecom_file_persists_raw_bytes_and_returns_filepart_payload(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"a,b\n1,2")
    uploaded = {
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/stable_report.csv",
        "name": "stable_report.csv",
        "mime_type": "text/csv",
        "size": 7,
    }

    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch(
            "services.file_upload.upload_to_payload",
            new=AsyncMock(return_value=uploaded),
        ),
    ):
        payload = await service._prepare_wecom_file(
            _message(), _context(), "user", "org",
        )

    assert payload == {
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/stable_report.csv",
        "name": "report.csv",
        "mime_type": "text/csv",
        "size": 7,
    }
    saved = list((tmp_path / "org" / "org" / "user" / "上传" / "企微").iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"a,b\n1,2"


@pytest.mark.asyncio
async def test_wecom_file_replay_reuses_stable_workspace_asset(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"first")
    upload = AsyncMock(return_value={
        "url": "https://cdn/report.csv",
        "workspace_path": "上传/企微/stable.csv",
        "name": "stable.csv",
        "mime_type": "text/csv",
        "size": 5,
    })
    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch("services.file_upload.upload_to_payload", new=upload),
    ):
        await service._prepare_wecom_file(_message(), _context(), "user", "org")
        service._download_and_decrypt_file.reset_mock()
        await service._prepare_wecom_file(_message(), _context(), "user", "org")

    service._download_and_decrypt_file.assert_not_awaited()
    assert upload.await_count == 2


@pytest.mark.asyncio
async def test_wecom_file_sanitizes_provider_filename(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"safe")
    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch(
            "services.file_upload.upload_to_payload",
            new=AsyncMock(return_value={
                "url": "https://cdn/passwd",
                "workspace_path": "上传/企微/stable_passwd",
                "name": "stable_passwd",
                "mime_type": "application/octet-stream",
                "size": 4,
            }),
        ),
    ):
        payload = await service._prepare_wecom_file(
            _message("../../passwd"), _context(), "user", "org",
        )

    assert payload["name"] == "passwd"
    assert ".." not in payload["workspace_path"]


@pytest.mark.asyncio
async def test_group_file_uses_channel_workspace(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"group")
    msg = _message()
    msg.chattype = "group"
    msg.chatid = "group-chat"
    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch(
            "services.file_upload.upload_to_payload",
            new=AsyncMock(return_value={"url": "https://cdn/group.csv"}),
        ),
    ):
        payload = await service._prepare_wecom_file(
            msg, _context(), "sender", "org",
        )

    assert payload["workspace_path"].startswith("上传/企微/")
    channel_root = tmp_path / "org" / "org" / "channels" / "wecom"
    assert len(list(channel_root.glob("*/上传/企微/*"))) == 1


@pytest.mark.asyncio
async def test_wecom_file_download_failure_does_not_enqueue_asset(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=None)

    with patch("core.config.get_settings", return_value=_settings(tmp_path)):
        payload = await service._prepare_wecom_file(
            _message(), _context(), "user", "org",
        )

    assert payload is None


@pytest.mark.asyncio
async def test_wecom_file_requires_dual_track_workspace_payload(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"content")
    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch(
            "services.file_upload.upload_to_payload",
            new=AsyncMock(return_value=None),
        ),
    ):
        payload = await service._prepare_wecom_file(
            _message(), _context(), "user", "org",
        )

    assert payload is None
    service._reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_wecom_file_rejects_missing_stable_msgid():
    service = _service()
    msg = _message()
    msg.msgid = None

    with pytest.raises(RuntimeError, match="WECOM_FILE_MSGID_MISSING"):
        await service._prepare_wecom_file(msg, _context(), "user", "org")


@pytest.mark.asyncio
async def test_wecom_file_workspace_write_failure_replies(tmp_path):
    service = _service()
    service._download_and_decrypt_file = AsyncMock(return_value=b"content")

    with (
        patch("core.config.get_settings", return_value=_settings(tmp_path)),
        patch(
            "services.wecom.wecom_file_mixin._atomic_write",
            side_effect=OSError("disk full"),
        ),
    ):
        payload = await service._prepare_wecom_file(
            _message(), _context(), "user", "org",
        )

    assert payload is None
    service._reply_text.assert_awaited_once_with(
        ANY, "文件保存失败，请稍后重新发送。",
    )


@pytest.mark.asyncio
async def test_download_file_rejects_missing_url():
    service = _service()
    msg = _message()
    msg.file_url = None
    context = _context()

    assert await service._download_and_decrypt_file(msg, context) is None
    service._reply_text.assert_awaited_once_with(
        context, "文件下载链接无效，请重新发送。",
    )


@pytest.mark.asyncio
async def test_download_file_reports_provider_failure():
    service = _service()
    context = _context()

    with patch(
        "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
        new=AsyncMock(return_value=None),
    ):
        result = await service._download_and_decrypt_file(_message(), context)

    assert result is None
    service._reply_text.assert_awaited_once_with(
        context, "文件下载失败，请稍后重新发送。",
    )
