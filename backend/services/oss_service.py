"""
阿里云 OSS 服务

提供图片上传、删除、URL 生成等功能。
支持从远程 URL 下载图片并上传到 OSS，通过 CDN 加速访问。
"""

import hashlib
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
import oss2
from loguru import logger

from core.config import settings


class OSSService:
    """阿里云 OSS 服务"""

    # 支持的图片格式
    SUPPORTED_IMAGE_FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}

    # 支持的视频格式
    SUPPORTED_VIDEO_FORMATS = {"mp4", "webm", "mov"}

    # 所有支持的格式
    SUPPORTED_FORMATS = SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS

    # 存储路径前缀
    IMAGE_PREFIX = "images"
    VIDEO_PREFIX = "videos"

    def __init__(self):
        """初始化 OSS 客户端"""
        if not all([
            settings.oss_access_key_id,
            settings.oss_access_key_secret,
            settings.oss_endpoint,
            settings.oss_bucket_name,
        ]):
            raise ValueError("OSS 配置不完整，请检查环境变量")

        auth = oss2.Auth(
            settings.oss_access_key_id,
            settings.oss_access_key_secret,
        )
        self.bucket = oss2.Bucket(
            auth,
            settings.oss_endpoint,
            settings.oss_bucket_name,
        )
        self.cdn_domain = settings.oss_cdn_domain

        logger.info(
            f"OSS service initialized: bucket={settings.oss_bucket_name}, "
            f"cdn={self.cdn_domain or 'not configured'}"
        )

    async def upload_from_url(
        self,
        url: str,
        user_id: str,
        category: str = "generated",
        media_type: str = "image",
    ) -> dict:
        """
        从远程 URL 下载文件并上传到 OSS

        Args:
            url: 远程文件 URL
            user_id: 用户 ID
            category: 分类（generated/uploaded/avatar）
            media_type: 媒体类型（image/video）

        Returns:
            {
                "object_key": "images/generated/2026/01/27/xxx.png",
                "url": "https://cdn.example.com/images/...",
                "size": 12345,
                "content_type": "image/png"
            }

        Raises:
            ValueError: URL 无效或格式不支持
            Exception: 下载或上传失败
        """
        logger.info(f"Uploading {media_type} from URL: user_id={user_id}, url={url[:100]}...")

        # 1. 下载文件（视频超时时间更长）
        timeout = 120.0 if media_type == "video" else 30.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Failed to download {media_type}: url={url}, error={e}")
                raise ValueError(f"{media_type}下载失败: {str(e)}")

        content = response.content
        content_type = response.headers.get("content-type", "")

        # 2. 确定文件扩展名
        ext = self._get_extension(url, content_type, media_type)

        # 验证格式
        if media_type == "video":
            if ext not in self.SUPPORTED_VIDEO_FORMATS:
                raise ValueError(f"不支持的视频格式: {ext}")
        else:
            if ext not in self.SUPPORTED_IMAGE_FORMATS:
                raise ValueError(f"不支持的图片格式: {ext}")

        # 3. 生成对象键（按日期分目录）
        prefix = self.VIDEO_PREFIX if media_type == "video" else self.IMAGE_PREFIX
        object_key = self._generate_object_key(user_id, category, ext, prefix)

        # 4. 上传到 OSS
        try:
            result = self.bucket.put_object(
                object_key,
                content,
                headers={"Content-Type": content_type or f"{media_type}/{ext}"},
            )
            logger.info(
                f"{media_type.capitalize()} uploaded: object_key={object_key}, "
                f"size={len(content)}, etag={result.etag}"
            )
        except oss2.exceptions.OssError as e:
            logger.error(f"OSS upload failed: object_key={object_key}, error={e}")
            raise Exception(f"OSS 上传失败: {str(e)}")

        # 5. 生成访问 URL
        access_url = self.get_url(object_key)

        return {
            "object_key": object_key,
            "url": access_url,
            "size": len(content),
            "content_type": content_type or f"{media_type}/{ext}",
        }

    def upload_bytes(
        self,
        content: bytes,
        user_id: str,
        ext: str = "png",
        category: str = "uploaded",
        content_type: Optional[str] = None,
    ) -> dict:
        """
        直接上传字节数据到 OSS

        Args:
            content: 图片字节数据
            user_id: 用户 ID
            ext: 文件扩展名
            category: 图片分类
            content_type: MIME 类型

        Returns:
            同 upload_from_url
        """
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"不支持的图片格式: {ext}")

        object_key = self._generate_object_key(user_id, category, ext)
        content_type = content_type or f"image/{ext}"

        try:
            result = self.bucket.put_object(
                object_key,
                content,
                headers={"Content-Type": content_type},
            )
            logger.info(
                f"Image uploaded: object_key={object_key}, "
                f"size={len(content)}, etag={result.etag}"
            )
        except oss2.exceptions.OssError as e:
            logger.error(f"OSS upload failed: object_key={object_key}, error={e}")
            raise Exception(f"OSS 上传失败: {str(e)}")

        return {
            "object_key": object_key,
            "url": self.get_url(object_key),
            "size": len(content),
            "content_type": content_type,
        }

    def delete(self, object_key: str) -> bool:
        """
        删除 OSS 对象

        Args:
            object_key: 对象键

        Returns:
            是否删除成功
        """
        try:
            self.bucket.delete_object(object_key)
            logger.info(f"Image deleted: object_key={object_key}")
            return True
        except oss2.exceptions.OssError as e:
            logger.error(f"OSS delete failed: object_key={object_key}, error={e}")
            return False

    def delete_by_url(self, url: str) -> bool:
        """
        根据 URL 删除 OSS 对象

        Args:
            url: 图片访问 URL

        Returns:
            是否删除成功
        """
        object_key = self._extract_object_key(url)
        if not object_key:
            logger.warning(f"Cannot extract object key from URL: {url}")
            return False
        return self.delete(object_key)

    def get_url(self, object_key: str) -> str:
        """
        获取对象的访问 URL

        优先使用 CDN 域名，否则使用 OSS 直链。

        Args:
            object_key: 对象键

        Returns:
            访问 URL
        """
        if self.cdn_domain:
            # 使用 CDN 加速域名
            return f"https://{self.cdn_domain}/{object_key}"
        else:
            # 使用 OSS 直链
            return f"https://{settings.oss_bucket_name}.{settings.oss_endpoint}/{object_key}"

    def exists(self, object_key: str) -> bool:
        """检查对象是否存在"""
        return self.bucket.object_exists(object_key)

    # ============================================================
    # 私有方法
    # ============================================================

    def _generate_object_key(
        self,
        user_id: str,
        category: str,
        ext: str,
        prefix: Optional[str] = None,
    ) -> str:
        """
        生成对象键

        格式：{prefix}/{category}/{yyyy}/{mm}/{dd}/{user_hash}_{uuid}.{ext}
        """
        now = datetime.utcnow()
        date_path = now.strftime("%Y/%m/%d")

        # 用户 ID 哈希（保护隐私）
        user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]

        # 唯一文件名
        file_id = uuid.uuid4().hex[:12]

        # 默认使用图片前缀
        prefix = prefix or self.IMAGE_PREFIX

        return f"{prefix}/{category}/{date_path}/{user_hash}_{file_id}.{ext}"

    def _get_extension(
        self,
        url: str,
        content_type: str,
        media_type: str = "image",
    ) -> str:
        """从 URL 或 Content-Type 获取文件扩展名"""
        # 先尝试从 URL 路径获取
        parsed = urlparse(url)
        path = parsed.path.lower()

        formats = self.SUPPORTED_VIDEO_FORMATS if media_type == "video" else self.SUPPORTED_IMAGE_FORMATS
        for fmt in formats:
            if path.endswith(f".{fmt}"):
                return fmt

        # 从 Content-Type 获取
        content_type_lower = content_type.lower()

        if media_type == "video":
            if "mp4" in content_type_lower:
                return "mp4"
            elif "webm" in content_type_lower:
                return "webm"
            elif "quicktime" in content_type_lower or "mov" in content_type_lower:
                return "mov"
            # 默认 mp4
            return "mp4"
        else:
            if "png" in content_type_lower:
                return "png"
            elif "jpeg" in content_type_lower or "jpg" in content_type_lower:
                return "jpg"
            elif "gif" in content_type_lower:
                return "gif"
            elif "webp" in content_type_lower:
                return "webp"
            # 默认 png
            return "png"

    def _extract_object_key(self, url: str) -> Optional[str]:
        """从 URL 提取对象键"""
        parsed = urlparse(url)

        # 移除开头的斜杠
        path = parsed.path.lstrip("/")

        # 检查是否是有效的图片路径
        if path.startswith(self.IMAGE_PREFIX):
            return path

        return None


# 全局单例
_oss_service: Optional[OSSService] = None


def get_oss_service() -> OSSService:
    """获取 OSS 服务单例"""
    global _oss_service
    if _oss_service is None:
        _oss_service = OSSService()
    return _oss_service
