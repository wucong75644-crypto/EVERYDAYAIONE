"""媒体任务结果的 OSS 与工作区持久化。"""

import asyncio
import random
from typing import Any

from loguru import logger

from services.oss_service import get_oss_service


_RESOLUTION_BASE = {"1K": 1024, "2K": 2048, "4K": 4096}
_ASPECT_RATIOS = {
    "1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2),
    "3:4": (3, 4), "4:3": (4, 3), "4:5": (4, 5),
    "5:4": (5, 4), "9:16": (9, 16), "16:9": (16, 9),
    "21:9": (21, 9),
}
_FRAMES_TO_SECONDS = {"10": 10, "15": 15, "25": 25}


def compute_image_dimensions(
    aspect_ratio: str,
    resolution: str | None = None,
) -> tuple[int, int]:
    """从宽高比和分辨率推算图片像素尺寸。"""
    base = _RESOLUTION_BASE.get(resolution or "1K", 1024)
    ratios = _ASPECT_RATIOS.get(aspect_ratio)
    if not ratios:
        return base, base
    width, height = ratios
    if width >= height:
        return base, int(base * height / width)
    return int(base * width / height), base


def compute_video_duration(n_frames: str) -> int:
    """从帧数参数推算视频时长。"""
    return _FRAMES_TO_SECONDS.get(str(n_frames), 10)


class MediaResultPersistence:
    """供任务完成服务复用的媒体持久化方法。"""

    async def _upload_urls_to_oss(
        self,
        urls: list[str],
        user_id: str,
        task_type: str,
        max_concurrent: int = 3,
        org_id: str | None = None,
    ) -> list[str]:
        if not urls:
            return []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def upload(url: str) -> str:
            async with semaphore:
                return await self._upload_single_to_oss(
                    url, user_id, task_type, org_id=org_id,
                )

        results = await asyncio.gather(
            *(upload(url) for url in urls),
            return_exceptions=True,
        )
        persisted: list[str] = []
        failures = 0
        for index, result in enumerate(results):
            if isinstance(result, Exception):
                failures += 1
                logger.warning(
                    f"OSS upload failed for url[{index}], using temporary URL | "
                    f"type={task_type} | error={result}"
                )
                persisted.append(urls[index])
            else:
                persisted.append(result)
        if failures:
            logger.warning(
                f"Batch OSS upload partial failure | type={task_type} | "
                f"total={len(urls)} | failed={failures} | "
                f"success={len(urls) - failures}"
            )
        return persisted

    async def _upload_single_to_oss(
        self,
        url: str,
        user_id: str,
        media_type: str,
        max_retries: int = 3,
        org_id: str | None = None,
    ) -> str:
        if not url or not url.strip():
            raise ValueError("Empty URL cannot be uploaded")
        try:
            oss_service = get_oss_service()
        except ValueError as error:
            logger.warning(
                "OSS not configured, using temporary URL (will expire) | "
                f"error={error}"
            )
            return url
        if oss_service.is_oss_url(url):
            return url

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                result = await oss_service.upload_from_url(
                    url=url,
                    user_id=user_id,
                    category="generated",
                    media_type=media_type,
                    org_id=org_id,
                )
                logger.info(
                    f"OSS upload success | type={media_type} | "
                    f"user_id={user_id} | object_key={result['object_key']} | "
                    f"attempt={attempt + 1}/{max_retries}"
                )
                return result["url"]
            except ValueError:
                raise
            except Exception as error:
                last_error = error
                logger.warning(
                    f"OSS upload attempt {attempt + 1}/{max_retries} failed | "
                    f"type={media_type} | error={error}"
                )
                if attempt == max_retries - 1:
                    raise Exception(
                        f"媒体持久化失败（已重试{max_retries}次）: {error}"
                    ) from error
                await asyncio.sleep(random.uniform(0, min(16.0, 2.0 ** attempt)))
        raise Exception(f"媒体持久化失败: {last_error}")

    async def _build_content_parts(
        self,
        urls: list[str],
        task_type: str,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            import json
            request_params = json.loads(request_params)
        if task_type == "image" and urls:
            from services.file_upload import persist_media_urls_to_workspace

            width, height = compute_image_dimensions(
                request_params.get("aspect_ratio", "1:1"),
                request_params.get("resolution"),
            )
            payloads = await persist_media_urls_to_workspace(
                urls=urls,
                user_id=task["user_id"],
                org_id=task.get("org_id"),
                media_type="image",
                meta={
                    "prompt": request_params.get("prompt") or "",
                    "model": task.get("model_id") or "",
                    "aspect_ratio": request_params.get("aspect_ratio"),
                    "resolution": request_params.get("resolution"),
                    "task_id": task.get("external_task_id"),
                },
                extra_fields={
                    "type": "image",
                    "width": width,
                    "height": height,
                },
            )
            return [payload for payload in payloads if payload.get("url")]

        if task_type == "video":
            duration = compute_video_duration(
                request_params.get("n_frames", "10"),
            )
            return [
                {"type": "video", "url": url, "duration": duration}
                for url in urls if url
            ]
        return []
