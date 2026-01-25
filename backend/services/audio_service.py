"""
音频服务

提供音频文件上传功能，使用 Supabase Storage。
"""

import uuid
from typing import Optional

from loguru import logger
from supabase import Client

from core.config import get_settings


class AudioService:
    """音频上传服务"""

    # 存储桶名称
    BUCKET_NAME = "uploads"
    # 音频存储路径前缀
    AUDIO_PREFIX = "audio"
    # 允许的音频类型
    ALLOWED_AUDIO_TYPES = {
        "audio/webm": "webm",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
    }
    # 最大文件大小 (25MB)
    MAX_FILE_SIZE = 25 * 1024 * 1024

    def __init__(self, db: Client):
        self.db = db
        self.settings = get_settings()

    async def upload_audio(
        self,
        user_id: str,
        file_data: bytes,
        content_type: str,
        filename: Optional[str] = None,
    ) -> dict:
        """
        上传音频到 Supabase Storage

        Args:
            user_id: 用户 ID
            file_data: 文件二进制数据
            content_type: MIME 类型
            filename: 原始文件名（可选）

        Returns:
            包含 audio_url, duration, size 的字典

        Raises:
            ValueError: 文件类型或大小不符合要求
        """
        # 验证文件类型
        if content_type not in self.ALLOWED_AUDIO_TYPES:
            raise ValueError(
                f"不支持的音频类型: {content_type}。"
                f"支持: {list(self.ALLOWED_AUDIO_TYPES.keys())}"
            )

        # 验证文件大小
        if len(file_data) > self.MAX_FILE_SIZE:
            raise ValueError(
                f"文件过大: {len(file_data) / 1024 / 1024:.1f}MB > "
                f"{self.MAX_FILE_SIZE / 1024 / 1024}MB"
            )

        # 生成唯一文件名
        extension = self.ALLOWED_AUDIO_TYPES[content_type]
        unique_filename = f"{uuid.uuid4().hex}.{extension}"
        file_path = f"{self.AUDIO_PREFIX}/{user_id}/{unique_filename}"

        try:
            # 上传到 Supabase Storage
            result = self.db.storage.from_(self.BUCKET_NAME).upload(
                path=file_path,
                file=file_data,
                file_options={"content-type": content_type},
            )

            logger.info(
                f"Audio uploaded: user_id={user_id}, "
                f"path={file_path}, size={len(file_data)}"
            )

            # 获取公开 URL
            public_url = self.db.storage.from_(self.BUCKET_NAME).get_public_url(
                file_path
            )

            # 音频时长由前端在录制时获取并传递，此处不解析
            # 如需服务端解析，可使用 ffprobe 命令或 pydub 库
            duration = 0.0

            return {
                "audio_url": public_url,
                "duration": duration,
                "size": len(file_data),
            }

        except Exception as e:
            logger.error(f"Upload audio failed: user_id={user_id}, error={e}")
            raise ValueError(f"上传失败: {e}") from e

    async def delete_audio(self, file_url: str) -> None:
        """
        删除音频文件

        Args:
            file_url: 文件的公开 URL

        Raises:
            ValueError: 删除失败
        """
        try:
            # 从 URL 中提取文件路径
            # 假设 URL 格式: https://.../storage/v1/object/public/uploads/audio/...
            if "/uploads/" in file_url:
                file_path = file_url.split("/uploads/")[1]
            else:
                raise ValueError("无效的文件 URL")

            # 删除文件
            self.db.storage.from_(self.BUCKET_NAME).remove([file_path])

            logger.info(f"Audio deleted: path={file_path}")

        except Exception as e:
            logger.error(f"Delete audio failed: url={file_url}, error={e}")
            raise ValueError(f"删除失败: {e}") from e

    async def get_audio_info(self, file_url: str) -> dict:
        """
        获取音频文件信息

        Args:
            file_url: 文件的公开 URL

        Returns:
            包含 duration 和 size 的字典

        Raises:
            ValueError: 获取失败
        """
        # 简化实现：从存储中获取文件元数据
        # 实际项目中可能需要存储到数据库或使用 ffmpeg 解析
        try:
            if "/uploads/" in file_url:
                file_path = file_url.split("/uploads/")[1]
            else:
                raise ValueError("无效的文件 URL")

            # Supabase Storage 不支持直接获取文件元数据
            # 音频信息应在上传时存储到数据库，或通过下载文件后解析获取
            # 当前返回默认值，实际使用时应从数据库查询
            return {
                "duration": 0.0,
                "size": 0,
            }

        except Exception as e:
            logger.error(f"Get audio info failed: url={file_url}, error={e}")
            raise ValueError(f"获取信息失败: {e}") from e
