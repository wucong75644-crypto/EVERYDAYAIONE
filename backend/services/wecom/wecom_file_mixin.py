"""
企微文件消息处理 Mixin

提供 WecomMessageService 的文��处理能力：
- 文件下载 + AES 解密
- 文件内容解析（PDF/Word/Excel/TXT/CSV/JSON 等）
- 文件上传 OSS 归档
- 构建 prompt 发给 AI 分析
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.wecom import WecomIncomingMessage, WecomReplyContext


class WecomFileMixin:
    """文件消息处理能力（被 WecomMessageService 继承）"""

    async def _handle_file(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
        org_id: Optional[str] = None,
    ) -> None:
        """处理文件消息：下载 → 解析文本 → 发给 AI 分析"""
        from services.wecom.file_parser import is_supported, parse_file

        filename = msg.file_name or "unknown"

        # 1. 检查文件类型是否支持
        if not is_supported(filename):
            ext = filename.rsplit(".", 1)[-1] if "." in filename else "未知"
            hint = (
                f"收到你的文件 {filename}，暂不支持 .{ext} 格式的解析。\n"
                "目前支持：PDF、Word、Excel、TXT、CSV、JSON、代码文件等。"
            )
            await self._reply_text(reply_ctx, hint)
            await self._update_assistant_message(message_id, hint)
            return

        # 2. 下载 + 解密
        raw_data = await self._download_and_decrypt_file(msg, reply_ctx)
        if raw_data is None:
            await self._update_assistant_message(message_id, "文件处理失败")
            return

        # 3. 解析文件内容 + 上传 OSS 归档
        file_text, truncated = parse_file(raw_data, filename)
        oss_url = await self._upload_file_to_oss(raw_data, user_id, filename)

        # 4. 保存用户消息到 DB
        self._save_file_message(conversation_id, filename, file_text, oss_url)

        # 5. 构建提示词发给 AI
        hint = "（注意：文件内容较长，以上仅为前 5000 字的节选）\n\n" if truncated else ""
        text_for_ai = (
            f"用户发送了文件「{filename}」，以下是文件内容：\n\n"
            f"{file_text}\n\n{hint}"
            "请分析这个文件的内容，给出要点总结或回答用户可能的问题。"
        )

        await self._handle_text(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text_content=text_for_ai,
            reply_ctx=reply_ctx,
            org_id=org_id,
        )

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

    def _save_file_message(
        self,
        conversation_id: str,
        filename: str,
        file_text: str,
        oss_url: Optional[str],
    ) -> None:
        """保存文件类用户消息到 DB"""
        content_parts: List[Dict[str, Any]] = []
        if oss_url:
            content_parts.append({"type": "file", "url": oss_url, "name": filename})
        content_parts.append({
            "type": "text",
            "text": f"[文件: {filename}]\n{file_text}",
        })
        self.db.table("messages").insert({
            "conversation_id": conversation_id,
            "role": "user",
            "content": content_parts,
            "status": "completed",
        }).execute()
        self.db.rpc("increment_message_count", {
            "conv_id": conversation_id,
            "p_org_id": getattr(self.db, "org_id", None),
        }).execute()

    @staticmethod
    async def _upload_file_to_oss(
        data: bytes, user_id: str, filename: str,
    ) -> Optional[str]:
        """将文件字节上传到 OSS，返回永久 URL"""
        try:
            from services.oss_service import get_oss_service
            from services.wecom.media_downloader import WecomMediaDownloader

            ext = WecomMediaDownloader._guess_ext(filename, "file")
            content_type = WecomMediaDownloader._guess_content_type(ext)
            oss_svc = get_oss_service()
            result = await asyncio.to_thread(
                oss_svc.upload_bytes,
                content=data,
                user_id=user_id,
                ext=ext,
                category="wecom_upload",
                content_type=content_type,
            )
            return result.get("url")
        except Exception as e:
            logger.warning(f"File OSS upload failed | filename={filename} | error={e}")
            return None
