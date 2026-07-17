"""
企微文件消息处理 Mixin

提供 WecomMessageService 的文件资产入口：
- 文件下载 + AES 解密
- 原始字节以稳定路径原子保存到共享 Workspace
- 同步 OSS，构建标准 FilePart 交给 Conversation Actor
"""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from schemas.wecom import WecomIncomingMessage, WecomReplyContext
from services.assets import AssetIdentity, identify_file
from services.wecom.media_downloader import DownloadedMedia


class WecomFileMixin:
    """文件消息处理能力（被 WecomMessageService 继承）"""

    async def _prepare_wecom_file(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        user_id: str,
        org_id: Optional[str] = None,
    ) -> dict[str, Any] | None:
        """保存原始文件资产并返回标准 FilePart payload。"""
        if not msg.msgid:
            raise RuntimeError("WECOM_FILE_MSGID_MISSING")
        scope_root = self._file_scope_root(msg, user_id, org_id)
        existing = await asyncio.to_thread(
            self._find_existing_file, scope_root, msg.msgid,
        )
        if existing:
            target = existing
            data = await asyncio.to_thread(target.read_bytes)
            identity = identify_file(
                data, stable_id=msg.msgid, provider_name=target.name.split("_", 1)[-1],
            )
        else:
            media = await self._download_and_decrypt_file(msg, reply_ctx)
            if media is None:
                return None
            identity = identify_file(
                media.data,
                stable_id=msg.msgid,
                provider_name=msg.file_name,
                content_disposition=media.content_disposition,
            )
            target = self._file_path(
                scope_root, msg.msgid, identity.canonical_name,
            )
            try:
                await asyncio.to_thread(_atomic_write, target, media.data)
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
        if payload and msg.chattype == "group":
            payload["workspace_path"] = str(target.relative_to(scope_root))
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
            "name": identity.canonical_name,
            "mime_type": identity.detected_mime_type,
            "size": size,
            "asset_identity": {
                "provider_name": identity.provider_name,
                "canonical_name": identity.canonical_name,
                "detected_mime_type": identity.detected_mime_type,
                "detection_source": identity.detection_source,
                "content_sha256": identity.content_sha256,
            },
        }

    @staticmethod
    def _file_path(
        scope_root: Path,
        msgid: str,
        filename: str,
    ) -> Path:
        token = uuid.uuid5(uuid.NAMESPACE_URL, f"wecom-file:{msgid}").hex[:16]
        return scope_root / "上传" / "企微" / f"{token}_{filename}"

    @classmethod
    def _find_existing_file(
        cls,
        scope_root: Path,
        msgid: str,
    ) -> Optional[Path]:
        token = uuid.uuid5(uuid.NAMESPACE_URL, f"wecom-file:{msgid}").hex[:16]
        matches = list((scope_root / "上传" / "企微").glob(f"{token}_*"))
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _file_scope_root(
        msg: WecomIncomingMessage,
        user_id: str,
        org_id: Optional[str],
    ) -> Path:
        from core.config import get_settings
        from core.workspace import (
            build_wecom_channel_workspace_owner,
            resolve_workspace_dir,
        )

        if msg.chattype != "group":
            return Path(resolve_workspace_dir(
                get_settings().file_workspace_root, user_id, org_id,
            ))
        if not org_id or not msg.chatid:
            raise RuntimeError("WECOM_GROUP_FILE_SCOPE_MISSING")
        owner_id = build_wecom_channel_workspace_owner(
            msg.corp_id, msg.chatid,
        )
        return Path(resolve_workspace_dir(
            get_settings().file_workspace_root, owner_id, org_id,
        ))

    async def _download_and_decrypt_file(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
    ) -> Optional[DownloadedMedia]:
        """下载企微文件 + AES 解密，失败时自动回复用户并返回 None"""
        if not msg.file_url:
            await self._reply_text(reply_ctx, "文件下载链接无效，请重新发送。")
            return None

        from services.wecom.media_downloader import WecomMediaDownloader
        downloader = WecomMediaDownloader()

        aeskey = msg.aeskeys.get(msg.file_url)
        media = await downloader.download_and_decrypt(msg.file_url, aeskey)
        if media is None:
            await self._reply_text(reply_ctx, "文件下载失败，请稍后重新发送。")
            return None

        return media


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
