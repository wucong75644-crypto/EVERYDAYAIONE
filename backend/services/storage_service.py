"""
存储服务

提供文件上传功能，使用阿里云 OSS + CDN 加速。
"""

import base64
from typing import Optional

from loguru import logger


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
    # 最大图片大小 (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024

    # 允许的文档类型
    ALLOWED_FILE_TYPES = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "text/csv": "csv",
        "text/markdown": "md",
        "application/json": "json",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/zip": "zip",
        "application/x-yaml": "yaml",
        "text/yaml": "yaml",
        "text/xml": "xml",
    }
    # 最大文档大小 (50MB)
    MAX_DOCUMENT_SIZE = 50 * 1024 * 1024

    def __init__(self, db):
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

    async def upload_file(
        self,
        user_id: str,
        file_data: bytes,
        content_type: str,
        filename: Optional[str] = None,
    ) -> dict:
        """
        上传文档文件到 OSS（当前仅支持 PDF）

        Args:
            user_id: 用户 ID
            file_data: 文件二进制数据
            content_type: MIME 类型
            filename: 原始文件名（可选）

        Returns:
            包含 url, name, mime_type, size 的字典

        Raises:
            ValueError: 文件类型或大小不符合要求
        """
        # 验证文件类型
        if content_type not in self.ALLOWED_FILE_TYPES:
            raise ValueError(
                f"不支持的文件类型: {content_type}。"
                f"支持: {list(self.ALLOWED_FILE_TYPES.keys())}"
            )

        # 验证文件大小
        if len(file_data) > self.MAX_DOCUMENT_SIZE:
            raise ValueError(
                f"文件过大: {len(file_data) / 1024 / 1024:.1f}MB > "
                f"{self.MAX_DOCUMENT_SIZE / 1024 / 1024:.0f}MB"
            )

        extension = self.ALLOWED_FILE_TYPES[content_type]

        try:
            oss_service = get_oss_service()
            result = oss_service.upload_bytes(
                content=file_data,
                user_id=user_id,
                ext=extension,
                category="documents",
                content_type=content_type,
            )

            logger.info(
                f"File uploaded to OSS: user_id={user_id}, "
                f"object_key={result['object_key']}, size={len(file_data)}"
            )

            return {
                "url": result["url"],
                "name": filename or f"document.{extension}",
                "mime_type": content_type,
                "size": len(file_data),
            }

        except Exception as e:
            logger.error(f"Upload file failed: user_id={user_id}, error={e}")
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
