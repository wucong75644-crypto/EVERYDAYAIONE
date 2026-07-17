"""
企微文件消息处理 Mixin

提供 WecomMessageService 的文件资产入口：
- 文件下载 + AES 解密
- 原始字节以稳定路径原子保存到共享 Workspace
- 同步 OSS，构建标准 FilePart 交给 Conversation Actor
"""

import asyncio
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from schemas.wecom import WecomIncomingMessage, WecomReplyContext


class WecomFileMixin:
    """文件消息处理能力（被 WecomMessageService 继承）"""

    async def _prepare_actor_file(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        org_id: Optional[str] = None,
    ) -> dict[str, Any] | None:
        """保存原始文件资产并返回标准 FilePart payload。"""
        if not msg.msgid:
            raise RuntimeError("WECOM_FILE_MSGID_MISSING")
        safe_name = Path(msg.file_name or "file.bin").name or "file.bin"
        target = self._actor_file_path(msg.msgid, safe_name, user_id, org_id)
        if not await asyncio.to_thread(target.is_file):
            raw_data = await self._download_and_decrypt_file(msg, reply_ctx)
            if raw_data is None:
                return None
            try:
                await asyncio.to_thread(_atomic_write, target, raw_data)
            except OSError as error:
                logger.error(
                    "Wecom file workspace write failed | "
                    f"msgid={msg.msgid} | user_id={user_id} | "
                    f"error={type(error).__name__}"
                )
                await self._reply_text(reply_ctx, "文件保存失败，请稍后重新发送。")
                return None

        from services.file_upload import upload_to_payload

        size = await asyncio.to_thread(lambda: target.stat().st_size)
        payload = await upload_to_payload(
            filename=target.name,
            size=size,
            output_dir=str(target.parent),
            user_id=user_id,
            org_id=org_id,
        )
        if (
            not payload
            or not payload.get("url")
            or not payload.get("workspace_path")
        ):
            await self._reply_text(reply_ctx, "文件保存失败，请稍后重新发送。")
            return None
        return {
            "url": str(payload["url"]),
            "workspace_path": str(payload["workspace_path"]),
            "name": safe_name,
            "mime_type": (
                mimetypes.guess_type(safe_name)[0]
                or "application/octet-stream"
            ),
            "size": size,
        }

    @staticmethod
    def _actor_file_path(
        msgid: str,
        filename: str,
        user_id: str,
        org_id: Optional[str],
    ) -> Path:
        from core.config import get_settings
        from core.workspace import resolve_workspace_dir

        root = Path(resolve_workspace_dir(
            get_settings().file_workspace_root, user_id, org_id,
        ))
        token = uuid.uuid5(uuid.NAMESPACE_URL, f"wecom-file:{msgid}").hex[:16]
        return root / "上传" / "企微" / f"{token}_{filename}"

    async def _download_and_decrypt_file(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
    ) -> Optional[bytes]:
        """下载企微文件 + AES 解密，失败时自动回复用户并返回 None"""
        if not msg.file_url:
            await self._reply_text(reply_ctx, "文件下载链接无效，请重新发送。")
            return None

        from services.wecom.media_downloader import WecomMediaDownloader
        downloader = WecomMediaDownloader()

        aeskey = msg.aeskeys.get(msg.file_url)
        raw_data = await downloader.download_and_decrypt(msg.file_url, aeskey)
        if raw_data is None:
            await self._reply_text(reply_ctx, "文件下载失败，请稍后重新发送。")
            return None

        return raw_data


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
