"""
企微多媒体资源下载 + AES 解密 + OSS 上传

长连接模式下，企微图片/文件/视频 URL 需要 aeskey 解密：
- 算法：AES-256-CBC
- 填充：PKCS#7（32 字节块大小）
- IV：取 aeskey 前 16 字节
- URL 有效期 ~5 分钟，必须尽快下载

自建应用模式下，URL 可直接访问（无 aeskey）。
"""

import asyncio
import base64
from typing import Optional

import httpx
from Crypto.Cipher import AES
from loguru import logger

from services.oss_service import get_oss_service

# 单文件大小上限（10MB）
MAX_FILE_SIZE = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 15  # 秒


class WecomMediaDownloader:
    """企微多媒体资源下载 + 解密 + 上传到 OSS"""

    async def download_and_store(
        self,
        url: str,
        user_id: str,
        aeskey: Optional[str] = None,
        media_type: str = "image",
        filename: Optional[str] = None,
    ) -> Optional[str]:
        """下载企微资源 → AES 解密（如有 aeskey）→ 上传 OSS → 返回永久 URL

        Args:
            url: 企微资源下载 URL
            user_id: 用户 ID（OSS 归档路径用）
            aeskey: AES 解密密钥（长连接模式），None 表示不需要解密
            media_type: "image" / "video" / "file"
            filename: 文件名（用于推断扩展名）

        Returns:
            OSS 永久 URL，失败返回 None
        """
        try:
            raw_data = await self._download(url)
            if raw_data is None:
                return None

            # AES 解密（长连接模式）
            if aeskey:
                raw_data = self._aes_decrypt(raw_data, aeskey)
                if raw_data is None:
                    return None

            # 推断扩展名
            ext = self._guess_ext(filename, media_type)

            # 上传到 OSS
            oss_svc = get_oss_service()
            result = await asyncio.to_thread(
                oss_svc.upload_bytes,
                content=raw_data,
                user_id=user_id,
                ext=ext,
                category="wecom_upload",
                content_type=self._guess_content_type(ext),
            )
            oss_url = result.get("url")
            logger.info(
                f"Wecom media uploaded | type={media_type} | "
                f"size={len(raw_data)} | oss_url={oss_url}"
            )
            return oss_url

        except Exception as e:
            logger.error(f"Wecom media download failed | url={url[:100]} | error={e}")
            return None

    async def _download(self, url: str) -> Optional[bytes]:
        """流式下载，限制 10MB"""
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        logger.warning(
                            f"Wecom media download HTTP {resp.status_code} | "
                            f"url={url[:100]}"
                        )
                        return None

                    chunks = []
                    total = 0
                    async for chunk in resp.aiter_bytes(8192):
                        total += len(chunk)
                        if total > MAX_FILE_SIZE:
                            logger.warning(
                                f"Wecom media too large (>{MAX_FILE_SIZE//1024//1024}MB) | "
                                f"url={url[:100]}"
                            )
                            return None
                        chunks.append(chunk)

                    return b"".join(chunks)

        except httpx.TimeoutException:
            logger.warning(f"Wecom media download timeout | url={url[:100]}")
            return None

    @staticmethod
    def _aes_decrypt(data: bytes, aeskey: str) -> Optional[bytes]:
        """AES-256-CBC 解密（企微长连接模式）

        密钥规格：
        - key = base64decode(aeskey)（32 字节）
        - IV = key[:16]
        - 填充：PKCS#7（块大小 32 字节）
        """
        try:
            # 企微 aeskey 是 43 字符 base64，需补齐 padding
            padded = aeskey + "=" * (-len(aeskey) % 4)
            key = base64.b64decode(padded)
            iv = key[:16]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(data)

            # PKCS#7 去填充
            pad_len = decrypted[-1]
            if pad_len < 1 or pad_len > 32:
                logger.warning(f"Wecom AES: invalid PKCS#7 padding={pad_len}")
                return None
            return decrypted[:-pad_len]

        except Exception as e:
            logger.error(f"Wecom AES decrypt failed | error={e}")
            return None

    @staticmethod
    def _guess_ext(filename: Optional[str], media_type: str) -> str:
        """推断文件扩展名"""
        if filename and "." in filename:
            return filename.rsplit(".", 1)[-1].lower()
        return {"image": "jpg", "video": "mp4", "file": "bin"}.get(media_type, "bin")

    @staticmethod
    def _guess_content_type(ext: str) -> str:
        """推断 MIME 类型"""
        mapping = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
            "pdf": "application/pdf", "doc": "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xls": "application/vnd.ms-excel",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        return mapping.get(ext, "application/octet-stream")
