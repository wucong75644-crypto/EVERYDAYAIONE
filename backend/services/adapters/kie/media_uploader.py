"""KIE 生成任务的临时输入素材交付。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from loguru import logger

from services.http_downloader import HttpDownloader
from services.oss_service import (
    is_configured_external_oss_url,
    normalize_external_oss_url,
)

from .client import KieClient, KieMediaUploadError


class KieMediaUploader:
    """将已有的永久素材 URL 临时交付给 KIE，并在单次适配器生命周期内复用。"""

    DEFAULT_MAX_IMAGE_SIZE_MB = 30

    def __init__(self, client: KieClient):
        self._client = client
        self._downloader = HttpDownloader()
        self._staged_urls: Dict[str, str] = {}
        self._failed_urls: Dict[str, KieMediaUploadError] = {}

    async def prepare_image_urls(
        self,
        image_urls: Optional[List[str]],
        max_size_mb: Optional[int] = None,
    ) -> Optional[List[str]]:
        """将自有 CDN/OSS 图片 URL 转换为 KIE 临时 URL，保持顺序并复用重复素材。"""
        if not image_urls:
            return image_urls

        max_size = (max_size_mb or self.DEFAULT_MAX_IMAGE_SIZE_MB) * 1024 * 1024
        prepared_urls: List[str] = []
        for source_url in image_urls:
            normalized_url = normalize_external_oss_url(source_url)
            if not is_configured_external_oss_url(normalized_url):
                raise KieMediaUploadError("KIE 输入素材必须来自工作区已上传图片")
            prepared_urls.append(
                await self._prepare_single_image(normalized_url, max_size)
            )
        return prepared_urls

    async def close(self) -> None:
        await self._downloader.close()

    async def _prepare_single_image(self, source_url: str, max_size: int) -> str:
        cached_url = self._staged_urls.get(source_url)
        if cached_url:
            return cached_url

        previous_failure = self._failed_urls.get(source_url)
        if previous_failure:
            raise previous_failure

        try:
            content, response_content_type = await self._downloader.download(
                url=source_url,
                user_id="kie-input-media",
                media_type="image",
                max_size=max_size,
            )
            content_type = self._resolve_content_type(source_url, response_content_type)
            if not content_type.startswith("image/"):
                raise KieMediaUploadError("KIE 输入素材不是图片文件")

            staged_url = await self._client.upload_file_stream(
                content=content,
                file_name=self._build_file_name(source_url, content_type),
                content_type=content_type,
            )
        except KieMediaUploadError as exc:
            self._failed_urls[source_url] = exc
            raise
        except ValueError as exc:
            upload_error = KieMediaUploadError(str(exc))
            self._failed_urls[source_url] = upload_error
            raise upload_error from exc
        except Exception as exc:
            upload_error = KieMediaUploadError(f"KIE 输入素材准备失败: {exc}")
            self._failed_urls[source_url] = upload_error
            raise upload_error from exc

        self._staged_urls[source_url] = staged_url
        logger.info("KIE input media staged | source_count=1")
        return staged_url

    @staticmethod
    def _resolve_content_type(source_url: str, response_content_type: str) -> str:
        content_type = response_content_type.split(";", 1)[0].strip().lower()
        if content_type:
            return content_type
        guessed_type, _ = mimetypes.guess_type(urlsplit(source_url).path)
        return guessed_type or "application/octet-stream"

    @staticmethod
    def _build_file_name(source_url: str, content_type: str) -> str:
        suffix = Path(unquote(urlsplit(source_url).path)).suffix.lower()
        if not suffix:
            suffix = mimetypes.guess_extension(content_type) or ".bin"
        return f"{uuid4().hex}{suffix}"
