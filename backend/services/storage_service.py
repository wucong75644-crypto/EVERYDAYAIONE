"""
存储服务

提供文件上传功能，使用 Supabase Storage。
"""

import base64
import uuid
from typing import Optional

from loguru import logger
from supabase import Client

from core.config import get_settings


class StorageService:
    """Supabase Storage 服务"""

    # 存储桶名称
    BUCKET_NAME = "uploads"
    # 图片存储路径前缀
    IMAGE_PREFIX = "images"
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
        self.db = db
        self.settings = get_settings()

    async def upload_image(
        self,
        user_id: str,
        file_data: bytes,
        content_type: str,
        filename: Optional[str] = None,
    ) -> str:
        """
        上传图片到 Supabase Storage

        Args:
            user_id: 用户 ID
            file_data: 文件二进制数据
            content_type: MIME 类型
            filename: 原始文件名（可选）

        Returns:
            图片的公开 URL

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

        # 生成唯一文件名
        extension = self.ALLOWED_IMAGE_TYPES[content_type]
        unique_filename = f"{uuid.uuid4().hex}.{extension}"
        file_path = f"{self.IMAGE_PREFIX}/{user_id}/{unique_filename}"

        try:
            # 上传到 Supabase Storage
            result = self.db.storage.from_(self.BUCKET_NAME).upload(
                path=file_path,
                file=file_data,
                file_options={"content-type": content_type},
            )

            logger.info(
                f"Image uploaded: user_id={user_id}, "
                f"path={file_path}, size={len(file_data)}"
            )

            # 获取公开 URL
            public_url = self.db.storage.from_(self.BUCKET_NAME).get_public_url(
                file_path
            )

            return public_url

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
            图片的公开 URL
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
