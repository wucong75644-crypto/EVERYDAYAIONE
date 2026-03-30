"""
WecomFileMixin 单元测试

覆盖：_handle_file 完整链路、_download_and_decrypt_file 各分支、
      _save_file_message DB 写入、_upload_file_to_oss 成功/失败
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


def _make_file_msg(
    filename: str = "report.txt",
    file_url: str = "https://wecom.example.com/file",
    aeskey: str | None = None,
) -> WecomIncomingMessage:
    aeskeys = {file_url: aeskey} if aeskey else {}
    return WecomIncomingMessage(
        msgid="msg_file_001",
        wecom_userid="user_abc",
        corp_id="corp1",
        chatid="user_abc",
        chattype=WecomChatType.SINGLE,
        msgtype=WecomMsgType.FILE,
        channel="smart_robot",
        file_url=file_url,
        file_name=filename,
        aeskeys=aeskeys,
    )


def _make_reply_ctx() -> WecomReplyContext:
    ws = MagicMock()
    ws.send_reply = AsyncMock()
    ws.send_stream_chunk = AsyncMock()
    return WecomReplyContext(
        channel="smart_robot",
        ws_client=ws,
        req_id="req_001",
    )


def _make_svc(db=None):
    db = db or _make_db_mock()
    svc = WecomMessageService(db)
    svc._reply_text = AsyncMock()
    svc._update_assistant_message = AsyncMock()
    svc._handle_text = AsyncMock()
    svc._upload_file_to_oss = AsyncMock(return_value="https://oss.example.com/file.txt")
    return svc


# ============================================================
# TestHandleFile — _handle_file 完整链路
# ============================================================


class TestHandleFile:
    """_handle_file 主流程测试"""

    @pytest.mark.asyncio
    async def test_unsupported_format_replies_and_updates_placeholder(self):
        """不支持的格式 → 回复提示 + 更新占位消息"""
        svc = _make_svc()
        msg = _make_file_msg(filename="archive.zip")
        ctx = _make_reply_ctx()

        await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        svc._reply_text.assert_called_once()
        assert "暂不支持" in svc._reply_text.call_args[0][1]
        svc._update_assistant_message.assert_called_once_with("a1", svc._reply_text.call_args[0][1])

    @pytest.mark.asyncio
    async def test_download_failure_updates_placeholder(self):
        """下载失败 → 更新占位消息为失败"""
        svc = _make_svc()
        msg = _make_file_msg(filename="data.csv")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=None),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        svc._update_assistant_message.assert_called_once_with("a1", "文件处理失败")
        svc._handle_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_calls_handle_text_with_file_content(self):
        """成功解析 → 调用 _handle_text，prompt 包含文件名和内容"""
        db = _make_db_mock()
        svc = _make_svc(db)

        msg = _make_file_msg(filename="notes.txt")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"Meeting notes for Monday"),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        svc._handle_text.assert_called_once()
        kwargs = svc._handle_text.call_args[1]
        assert "notes.txt" in kwargs["text_content"]
        assert "Meeting notes for Monday" in kwargs["text_content"]
        assert kwargs["user_id"] == "uid1"
        assert kwargs["conversation_id"] == "conv1"
        assert kwargs["message_id"] == "a1"

    @pytest.mark.asyncio
    async def test_success_saves_file_message_to_db(self):
        """成功解析 → 保存 file+text 内容到 DB"""
        db = _make_db_mock()
        svc = _make_svc(db)

        msg = _make_file_msg(filename="data.csv")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"a,b\n1,2"),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        # 验证 messages.insert 被调用
        db.table.assert_any_call("messages")
        db.rpc.assert_called_with("increment_message_count", {"conv_id": "conv1"})

    @pytest.mark.asyncio
    async def test_truncated_content_includes_hint(self):
        """超长文件 → prompt 中包含截断提示"""
        svc = _make_svc()
        msg = _make_file_msg(filename="big.txt")
        ctx = _make_reply_ctx()

        long_content = b"A" * 6000

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=long_content),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        kwargs = svc._handle_text.call_args[1]
        assert "节选" in kwargs["text_content"]

    @pytest.mark.asyncio
    async def test_no_file_url_replies_error(self):
        """file_url 为空 → 回复错误"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt", file_url="")
        msg.file_url = None
        ctx = _make_reply_ctx()

        await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        svc._reply_text.assert_called_once()
        assert "下载链接无效" in svc._reply_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_oss_upload_failure_still_proceeds(self):
        """OSS 上传失败不影响 AI 分析"""
        svc = _make_svc()
        svc._upload_file_to_oss = AsyncMock(return_value=None)

        msg = _make_file_msg(filename="test.txt")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"some content"),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx)

        svc._handle_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_org_id_passed_to_handle_text(self):
        """org_id 正确传递到 _handle_text"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"content"),
        ):
            await svc._handle_file("uid1", "conv1", "a1", msg, ctx, org_id="org_123")

        kwargs = svc._handle_text.call_args[1]
        assert kwargs["org_id"] == "org_123"


# ============================================================
# TestDownloadAndDecryptFile
# ============================================================


class TestDownloadAndDecryptFile:
    """_download_and_decrypt_file 各分支"""

    @pytest.mark.asyncio
    async def test_no_file_url_replies_invalid(self):
        """file_url 为 None → 回复链接无效"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt")
        msg.file_url = None
        ctx = _make_reply_ctx()

        result = await svc._download_and_decrypt_file(msg, ctx)

        assert result is None
        svc._reply_text.assert_called_once()
        assert "链接无效" in svc._reply_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_download_failure_replies_error(self):
        """下载返回 None → 回复下载失败"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=None),
        ):
            result = await svc._download_and_decrypt_file(msg, ctx)

        assert result is None
        svc._reply_text.assert_called_once()
        assert "下载失败" in svc._reply_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_success_returns_bytes(self):
        """成功 → 返回 bytes"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"file data"),
        ):
            result = await svc._download_and_decrypt_file(msg, ctx)

        assert result == b"file data"
        svc._reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_aeskey_to_downloader(self):
        """有 aeskey → 传递给 downloader"""
        svc = _make_svc()
        msg = _make_file_msg(filename="test.txt", aeskey="my_aes_key")
        ctx = _make_reply_ctx()

        with patch(
            "services.wecom.media_downloader.WecomMediaDownloader.download_and_decrypt",
            new=AsyncMock(return_value=b"decrypted"),
        ) as mock_dd:
            await svc._download_and_decrypt_file(msg, ctx)
            mock_dd.assert_called_once_with(msg.file_url, "my_aes_key")


# ============================================================
# TestSaveFileMessage
# ============================================================


class TestSaveFileMessage:
    """_save_file_message DB 写入"""

    def test_with_oss_url_includes_file_part(self):
        """有 OSS URL → content 包含 file 类型"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._save_file_message("conv1", "report.pdf", "文件内容", "https://oss/file.pdf")

        insert_call = db.table("messages").insert
        insert_call.assert_called_once()
        data = insert_call.call_args[0][0]
        assert data["conversation_id"] == "conv1"
        assert data["role"] == "user"
        assert data["status"] == "completed"

        types = [p["type"] for p in data["content"]]
        assert "file" in types
        assert "text" in types

    def test_without_oss_url_only_text_part(self):
        """无 OSS URL → content 只有 text 类型"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._save_file_message("conv1", "notes.txt", "内容", None)

        data = db.table("messages").insert.call_args[0][0]
        types = [p["type"] for p in data["content"]]
        assert "file" not in types
        assert "text" in types

    def test_increments_message_count(self):
        """保存后调用 increment_message_count"""
        db = _make_db_mock()
        svc = WecomMessageService(db)

        svc._save_file_message("conv1", "f.txt", "text", None)

        db.rpc.assert_called_with("increment_message_count", {"conv_id": "conv1"})


# ============================================================
# TestUploadFileToOss
# ============================================================


class TestUploadFileToOss:
    """_upload_file_to_oss 上传"""

    @pytest.mark.asyncio
    async def test_success_returns_url(self):
        """上传成功 → 返回 OSS URL"""
        mock_oss = MagicMock()
        mock_oss.upload_bytes.return_value = {"url": "https://oss/test.txt"}

        with patch(
            "services.oss_service.get_oss_service",
            return_value=mock_oss,
        ), patch(
            "asyncio.to_thread",
            new=AsyncMock(return_value={"url": "https://oss/test.txt"}),
        ):
            result = await WecomMessageService._upload_file_to_oss(
                b"data", "uid1", "test.txt",
            )

        assert result == "https://oss/test.txt"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """上传异常 → 返回 None"""
        with patch(
            "services.oss_service.get_oss_service",
            side_effect=Exception("OSS down"),
        ):
            result = await WecomMessageService._upload_file_to_oss(
                b"data", "uid1", "test.txt",
            )

        assert result is None
