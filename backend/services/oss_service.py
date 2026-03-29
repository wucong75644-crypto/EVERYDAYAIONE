"""
阿里云 OSS 服务

提供图片上传、删除、URL 生成等功能。
支持从远程 URL 下载图片并上传到 OSS，通过 CDN 加速访问。
"""

import hashlib
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import oss2
from loguru import logger

from core.config import settings
from core.exceptions import AppException
from services.http_downloader import HttpDownloader


class OSSService:
    """阿里云 OSS 服务"""

    # 支持的图片格式
    SUPPORTED_IMAGE_FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}

    # 支持的视频格式
    SUPPORTED_VIDEO_FORMATS = {"mp4", "webm", "mov"}

    # 支持的文档格式
    SUPPORTED_DOC_FORMATS = {
        "pdf", "txt", "csv", "md", "json", "yaml", "xml",
        "xls", "xlsx", "ppt", "pptx", "doc", "docx", "zip",
    }

    # 所有支持的格式
    SUPPORTED_FORMATS = SUPPORTED_IMAGE_FORMATS | SUPPORTED_VIDEO_FORMATS | SUPPORTED_DOC_FORMATS

    # 存储路径前缀
    IMAGE_PREFIX = "images"
    VIDEO_PREFIX = "videos"

    # 文件大小限制（字节）
    MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
    MAX_VIDEO_SIZE = 500 * 1024 * 1024  # 500MB

    def __init__(self):
        """
        初始化 OSS 客户端

        支持双端点配置：
        - 内网端点（oss_internal_endpoint）：用于上传，免流量费
        - 外网端点（oss_endpoint）：用于生成 CDN URL
        """
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

        # 优先使用内网端点（免流量费），否则使用外网端点
        upload_endpoint = settings.oss_internal_endpoint or settings.oss_endpoint
        self.bucket = oss2.Bucket(
            auth,
            upload_endpoint,
            settings.oss_bucket_name,
        )
        self.cdn_domain = settings.oss_cdn_domain
        self.external_endpoint = settings.oss_endpoint  # 用于生成外部访问 URL
        self._downloader = HttpDownloader()

        # 日志：区分内外网
        endpoint_type = "internal" if settings.oss_internal_endpoint else "external"
        logger.info(
            f"OSS service initialized: bucket={settings.oss_bucket_name}, "
            f"endpoint={upload_endpoint} ({endpoint_type}), "
            f"cdn={self.cdn_domain or 'not configured'}"
        )

    async def close(self) -> None:
        """关闭 HTTP 下载器连接池"""
        await self._downloader.close()

    async def _validate_and_upload(
        self,
        content: bytes,
        content_type: str,
        url: str,
        user_id: str,
        category: str,
        media_type: str,
        org_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        验证格式并上传到 OSS

        Args:
            content: 文件内容
            content_type: Content-Type
            url: 原始 URL（用于提取扩展名）
            user_id: 用户 ID
            category: 分类
            media_type: 媒体类型
            org_id: 企业ID（散客为None）

        Returns:
            (object_key, access_url): 对象键和访问 URL

        Raises:
            ValueError: 格式不支持
            AppException: OSS 上传失败
        """
        # 确定文件扩展名
        ext = self._get_extension(url, content_type, media_type)

        # 验证格式
        if media_type == "video":
            if ext not in self.SUPPORTED_VIDEO_FORMATS:
                raise ValueError(f"不支持的视频格式: {ext}")
        else:
            if ext not in self.SUPPORTED_IMAGE_FORMATS:
                raise ValueError(f"不支持的图片格式: {ext}")

        # 生成对象键（按日期分目录）
        prefix = self.VIDEO_PREFIX if media_type == "video" else self.IMAGE_PREFIX
        object_key = self._generate_object_key(user_id, category, ext, prefix, org_id=org_id)

        # 上传到 OSS（使用线程池避免阻塞event loop）
        try:
            import asyncio
            # 将同步OSS上传放到线程池执行，避免阻塞worker
            result = await asyncio.to_thread(
                self.bucket.put_object,
                object_key,
                content,
                headers={"Content-Type": content_type or f"{media_type}/{ext}"},
            )
            logger.info(
                f"{media_type.capitalize()} uploaded: object_key={object_key}, "
                f"size={len(content)}, etag={result.etag}"
            )
        except (ValueError, AppException):
            raise
        except oss2.exceptions.OssError as e:
            logger.error(
                f"OSS upload failed | user_id={user_id} | object_key={object_key} | "
                f"media_type={media_type} | error={str(e)}"
            )
            # 脱敏：不暴露OSS内部错误详情
            raise AppException(
                code="OSS_UPLOAD_ERROR",
                message="OSS 上传失败，请稍后重试",
                status_code=500,
            )

        # 生成访问 URL
        access_url = self.get_url(object_key)
        return object_key, access_url

    async def upload_from_url(
        self,
        url: str,
        user_id: str,
        category: str = "generated",
        media_type: str = "image",
        org_id: Optional[str] = None,
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
            ValueError: URL 无效、格式不支持或文件过大
            Exception: 下载或上传失败
        """
        logger.info(f"Uploading {media_type} from URL: user_id={user_id}, url={url[:100]}...")

        # 1. 下载文件
        max_size = self.MAX_VIDEO_SIZE if media_type == "video" else self.MAX_IMAGE_SIZE
        content, content_type = await self._downloader.download(url, user_id, media_type, max_size)

        # 2. 验证并上传
        object_key, access_url = await self._validate_and_upload(
            content, content_type, url, user_id, category, media_type, org_id=org_id
        )

        # 3. 返回结果
        return {
            "object_key": object_key,
            "url": access_url,
            "size": len(content),
            "content_type": content_type or f"{media_type}/{self._get_extension(url, content_type, media_type)}",
        }

    def upload_bytes(
        self,
        content: bytes,
        user_id: str,
        ext: str = "png",
        category: str = "uploaded",
        content_type: Optional[str] = None,
        org_id: Optional[str] = None,
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
            raise ValueError(f"不支持的文件格式: {ext}")

        object_key = self._generate_object_key(user_id, category, ext, org_id=org_id)
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
            # 脱敏：不暴露OSS内部错误详情
            raise Exception("OSS 上传失败，请稍后重试")

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

        优先使用 CDN 域名，否则使用 OSS 外网直链。

        Args:
            object_key: 对象键

        Returns:
            访问 URL
        """
        if self.cdn_domain:
            # 使用 CDN 加速域名
            return f"https://{self.cdn_domain}/{object_key}"
        else:
            # 使用 OSS 外网直链（使用 external_endpoint）
            return f"https://{settings.oss_bucket_name}.{self.external_endpoint}/{object_key}"

    def is_oss_url(self, url: str) -> bool:
        """
        检查 URL 是否已经是 OSS/CDN URL

        用于避免重复上传。使用精确的域名匹配防止误判。
        """
        if not url or not url.strip():
            return False

        try:
            parsed = urlparse(url)
            hostname = parsed.hostname

            if not hostname:
                return False

            # 精确匹配 CDN 域名
            if self.cdn_domain and hostname.lower() == self.cdn_domain.lower():
                return True

            # 精确匹配 OSS 域名
            oss_domain = f"{settings.oss_bucket_name}.{self.external_endpoint}"
            if hostname.lower() == oss_domain.lower():
                return True

            return False

        except Exception as e:
            logger.warning(f"Failed to parse URL: {url[:50]}... | error={e}")
            return False

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
        org_id: Optional[str] = None,
    ) -> str:
        """
        生成对象键

        企业用户: org/{org_id}/{prefix}/{category}/{date}/{hash}_{uuid}.{ext}
        散客:     personal/{user_hash}/{prefix}/{category}/{date}/{hash}_{uuid}.{ext}
        """
        now = datetime.now(timezone.utc)
        date_path = now.strftime("%Y/%m/%d")

        # 用户 ID 哈希（保护隐私）
        user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]

        # 唯一文件名
        file_id = uuid.uuid4().hex[:12]

        # 默认使用图片前缀
        prefix = prefix or self.IMAGE_PREFIX

        # 企业/散客路径隔离
        if org_id:
            tenant = f"org/{org_id}"
        else:
            tenant = f"personal/{user_hash}"

        return f"{tenant}/{prefix}/{category}/{date_path}/{user_hash}_{file_id}.{ext}"

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

        # 检查是否是有效的 OSS 路径（旧格式或多租户新格式）
        valid_prefixes = (
            self.IMAGE_PREFIX, self.VIDEO_PREFIX,
            "org/", "personal/",
        )
        if any(path.startswith(p) for p in valid_prefixes):
            return path

        return None


# 全局单例（线程安全）

_oss_service: Optional[OSSService] = None
_oss_lock = threading.Lock()


def get_oss_service() -> OSSService:
    """
    获取 OSS 服务单例（线程安全）

    使用双重检查锁定模式确保多线程环境下只创建一个实例。
    """
    global _oss_service

    # 第一次检查（无锁，快速路径）
    if _oss_service is not None:
        return _oss_service

    # 获取锁
    with _oss_lock:
        # 第二次检查（有锁，防止多个线程同时初始化）
        if _oss_service is None:
            _oss_service = OSSService()

    return _oss_service
