"""
存储服务

提供文件上传功能，使用阿里云 OSS + CDN 加速。
"""

import base64
from typing import Optional

from loguru import logger
from supabase import Client

from services.oss_service import get_oss_service


class StorageService:
    """存储服务（使用阿里云 OSS）"""

    # 允许的图片类型
    ALLOWED_IMAGE_TYPES = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    # 最大文件大小 (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024

    def __init__(self, db: Client):
        """
        初始化存储服务

        Args:
            db: Supabase 客户端（保留参数以保持 API 兼容性）
        """
        self.db = db

    async def upload_image(
        self,
        user_id: str,
        file_data: bytes,
        content_type: str,
        filename: Optional[str] = None,
    ) -> str:
        """
        上传图片到 OSS

        Args:
            user_id: 用户 ID
            file_data: 文件二进制数据
            content_type: MIME 类型
            filename: 原始文件名（可选）

        Returns:
            图片的 CDN URL

        Raises:
            ValueError: 文件类型或大小不符合要求
        """
        # 验证文件类型
        if content_type not in self.ALLOWED_IMAGE_TYPES:
            raise ValueError(
                f"不支持的图片类型: {content_type}。"
                f"支持: {list(self.ALLOWED_IMAGE_TYPES.keys())}"
            )

        # 验证文件大小
        if len(file_data) > self.MAX_FILE_SIZE:
            raise ValueError(
                f"文件过大: {len(file_data) / 1024 / 1024:.1f}MB > "
                f"{self.MAX_FILE_SIZE / 1024 / 1024}MB"
            )

        # 获取文件扩展名
        extension = self.ALLOWED_IMAGE_TYPES[content_type]

        try:
            # 上传到 OSS
            oss_service = get_oss_service()
            result = oss_service.upload_bytes(
                content=file_data,
                user_id=user_id,
                ext=extension,
                category="uploaded",
                content_type=content_type,
            )

            logger.info(
                f"Image uploaded to OSS: user_id={user_id}, "
                f"object_key={result['object_key']}, size={len(file_data)}"
            )

            return result["url"]

        except Exception as e:
            logger.error(f"Upload image failed: user_id={user_id}, error={e}")
            raise ValueError(f"上传失败: {e}") from e

    async def upload_base64_image(
        self,
        user_id: str,
        base64_data: str,
    ) -> str:
        """
        上传 base64 编码的图片

        Args:
            user_id: 用户 ID
            base64_data: base64 数据（可包含 data URL 前缀）

        Returns:
            图片的 CDN URL
        """
        # 解析 data URL
        if base64_data.startswith("data:"):
            # 格式: data:image/png;base64,xxxx
            header, encoded = base64_data.split(",", 1)
            content_type = header.split(";")[0].split(":")[1]
        else:
            # 假设是纯 base64，默认 jpeg
            encoded = base64_data
            content_type = "image/jpeg"

        # 解码 base64
        try:
            file_data = base64.b64decode(encoded)
        except Exception as e:
            raise ValueError(f"无效的 base64 数据: {e}") from e

        return await self.upload_image(
            user_id=user_id,
            file_data=file_data,
            content_type=content_type,
        )
