"""
统一任务完成处理服务

Webhook 和轮询兜底的统一入口，保证：
1. 幂等性：已完成的任务不重复处理
2. 格式一致：统一走 handler.on_complete/on_error
3. OSS 上传：在调用 handler 前完成（临时 URL → 持久化）
"""

from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone

from loguru import logger
from supabase import Client

from schemas.message import ImagePart, VideoPart
from services.adapters.base import (
    ImageGenerateResult,
    VideoGenerateResult,
    TaskStatus,
)
from services.oss_service import get_oss_service

TaskResult = Union[ImageGenerateResult, VideoGenerateResult]


# ============================================================
# 元数据计算常量
# ============================================================

# 分辨率基准像素（长边）
_RESOLUTION_BASE: Dict[str, int] = {"1K": 1024, "2K": 2048, "4K": 4096}

# 宽高比 → (w, h) 比例因子
_ASPECT_RATIOS: Dict[str, Tuple[int, int]] = {
    "1:1": (1, 1), "2:3": (2, 3), "3:2": (3, 2),
    "3:4": (3, 4), "4:3": (4, 3), "4:5": (4, 5),
    "5:4": (5, 4), "9:16": (9, 16), "16:9": (16, 9),
    "21:9": (21, 9),
}

# KIE n_frames 值 → 视频秒数
_FRAMES_TO_SECONDS: Dict[str, int] = {"10": 10, "15": 15, "25": 25}


def _compute_image_dimensions(
    aspect_ratio: str,
    resolution: Optional[str] = None,
) -> Tuple[int, int]:
    """从宽高比和分辨率推算图片像素尺寸（长边=base）"""
    base = _RESOLUTION_BASE.get(resolution or "1K", 1024)
    ratios = _ASPECT_RATIOS.get(aspect_ratio)
    if not ratios:
        return base, base
    w, h = ratios
    if w >= h:
        return base, int(base * h / w)
    return int(base * w / h), base


def _compute_video_duration(n_frames: str) -> int:
    """从 n_frames 参数推算视频时长（秒）"""
    return _FRAMES_TO_SECONDS.get(str(n_frames), 10)


class TaskCompletionService:
    """
    统一任务完成处理入口

    接收标准 ImageGenerateResult / VideoGenerateResult，
    不关心结果来自 Webhook 还是轮询、来自哪个 Provider。
    """

    def __init__(self, db: Client):
        self.db = db

    def get_task(self, external_task_id: str) -> Optional[Dict[str, Any]]:
        """根据 external_task_id 查询任务"""
        try:
            result = (
                self.db.table("tasks")
                .select("*")
                .eq("external_task_id", external_task_id)
                .maybe_single()
                .execute()
            )
            return result.data if result.data else None
        except Exception as e:
            logger.warning(
                f"get_task query failed | task_id={external_task_id} | error={e}"
            )
            return None

    async def process_result(self, external_task_id: str, result: TaskResult) -> bool:
        """
        统一处理入口（原子锁防并发）

        通过 version 字段的乐观锁机制，原子抢占任务处理权，
        防止 Webhook 和轮询同时处理同一任务。

        Args:
            external_task_id: 外部任务 ID
            result: 统一结果（ImageGenerateResult 或 VideoGenerateResult）

        Returns:
            True = 已处理（含幂等跳过），False = 处理失败
        """
        # pending/processing 状态忽略（轮询场景，任务仍在进行中）
        if result.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            return True

        # 1. 查询当前任务状态（获取version用于乐观锁）
        task = self.get_task(external_task_id)
        if not task:
            logger.warning(f"Task not found | task_id={external_task_id}")
            return False

        # 2. 幂等检查：任务已经是终态，跳过处理
        if task['status'] in ['completed', 'failed', 'cancelled']:
            logger.info(
                f"Task already {task['status']}, skipping | "
                f"task_id={external_task_id}"
            )
            return True

        # 3. 只处理 pending/running 状态的任务
        if task['status'] not in ['pending', 'running']:
            logger.warning(
                f"Task in unexpected status | task_id={external_task_id} | "
                f"status={task['status']}"
            )
            return False

        # 4. 乐观锁抢占：通过 version 字段原子更新
        # 只有version未变化的任务才会被更新（防止并发冲突）
        current_version = task.get('version', 1)
        lock_update = (
            self.db.table("tasks")
            .update({
                "version": current_version + 1,
                "started_at": task.get("started_at") or datetime.now(timezone.utc).isoformat()
            })
            .eq("external_task_id", external_task_id)
            .eq("version", current_version)  # 乐观锁条件
            .in_("status", ["pending", "running"])  # 双重保险
            .execute()
        )

        # 5. 检查是否抢到锁
        if not lock_update.data:
            logger.info(
                f"Task lock failed (concurrent processing) | task_id={external_task_id}"
            )
            return True  # 其他进程已处理，幂等返回成功

        # 6. 成功抢到锁，保留原始 task（get_task 返回完整行）
        #    lock_update.data[0] 可能仅包含更新字段，缺少 type 等列

        # 7. 根据结果状态分发处理
        try:
            if result.status == TaskStatus.SUCCESS:
                return await self._handle_success(task, result)
            else:
                return await self._handle_failure(task, result)
        except Exception as e:
            # 处理失败：记录错误，让轮询兜底重试
            # 注意：不回退状态，保持 pending/running 以便下次轮询重试
            logger.error(
                f"Task completion failed | "
                f"task_id={external_task_id} | error={e}",
                exc_info=True
            )
            return False

    async def _handle_success(self, task: Dict[str, Any], result: TaskResult) -> bool:
        """处理成功结果"""
        external_task_id = task["external_task_id"]
        task_type = task["type"]
        user_id = task["user_id"]

        # 1. 提取媒体 URL
        raw_urls = self._extract_urls(result, task_type)

        # 2. OSS 上传（临时 URL → 持久化）
        oss_urls = await self._upload_urls_to_oss(raw_urls, user_id, task_type)

        # 3. 构建 ContentPart 列表（含元数据）
        content_parts = self._build_content_parts(oss_urls, task_type, task)

        # 4. 空结果检查
        if not content_parts:
            logger.warning(
                f"No result content | task_id={external_task_id} | "
                f"raw_urls={raw_urls}"
            )
            return await self._handle_failure(task, _empty_result(
                result, "NO_RESULT", "生成结果为空",
            ))

        # 5. 图片任务统一走批次处理（含 num_images=1）
        if task_type == "image" and task.get("batch_id"):
            from services.batch_completion_service import BatchCompletionService
            batch_svc = BatchCompletionService(self.db)
            return await batch_svc.handle_image_complete(task, content_parts)

        # 6. 其他任务（video）走原有 Handler 路径
        handler = self._create_handler(task_type)
        await handler.on_complete(
            task_id=external_task_id,
            result=content_parts,
        )

        logger.info(
            f"Task completed via unified service | task_id={external_task_id} | "
            f"type={task_type} | urls={len(oss_urls)}"
        )
        return True

    async def _handle_failure(self, task: Dict[str, Any], result: TaskResult) -> bool:
        """处理失败结果（含 smart_mode 异步重试）"""
        external_task_id = task["external_task_id"]
        task_type = task["type"]

        # Smart mode 异步重试：尝试用替代模型重新提交
        from services.async_retry_service import AsyncRetryService
        retry_svc = AsyncRetryService(self.db)
        if await retry_svc.attempt_retry(task, result):
            return True

        # 图片任务统一走批次处理
        if task_type == "image" and task.get("batch_id"):
            from services.batch_completion_service import BatchCompletionService
            batch_svc = BatchCompletionService(self.db)
            return await batch_svc.handle_image_failure(
                task,
                error_code=result.fail_code or "UNKNOWN",
                error_message=result.fail_msg or "任务失败",
            )

        # 其他任务走原有 Handler 路径
        handler = self._create_handler(task_type)
        await handler.on_error(
            task_id=external_task_id,
            error_code=result.fail_code or "UNKNOWN",
            error_message=result.fail_msg or "任务失败",
        )

        logger.info(
            f"Task failed via unified service | task_id={external_task_id} | "
            f"type={task_type} | error={result.fail_msg}"
        )
        return True

    # ========================================
    # 辅助方法
    # ========================================

    def _extract_urls(self, result: TaskResult, task_type: str) -> List[str]:
        """
        从统一结果中提取媒体 URL 列表

        过滤掉空白或无效的 URL。
        """
        urls = []

        if task_type == "image" and isinstance(result, ImageGenerateResult):
            urls = result.image_urls or []
        elif task_type == "video" and isinstance(result, VideoGenerateResult):
            urls = [result.video_url] if result.video_url else []

        # 过滤空白 URL
        return [url for url in urls if url and url.strip()]

    async def _upload_urls_to_oss(
        self,
        urls: List[str],
        user_id: str,
        task_type: str,
        max_concurrent: int = 3,
    ) -> List[str]:
        """
        批量上传媒体到 OSS（并发上传）

        KIE 等 Provider 返回的临时 URL 会过期，需上传到 OSS 持久化。
        使用并发上传提升性能，同时限制并发数防止资源耗尽。

        Args:
            urls: URL 列表
            user_id: 用户 ID
            task_type: 任务类型
            max_concurrent: 最大并发数（默认 3）

        Returns:
            OSS URL 列表
        """
        if not urls:
            return []

        # 创建信号量限制并发数
        import asyncio
        semaphore = asyncio.Semaphore(max_concurrent)

        async def upload_with_limit(url: str) -> str:
            """带限流的上传"""
            async with semaphore:
                return await self._upload_single_to_oss(url, user_id, task_type)

        # 并发上传所有 URL（部分成功模式）
        results = await asyncio.gather(
            *[upload_with_limit(url) for url in urls],
            return_exceptions=True  # 收集所有结果，不因单个失败丢弃全部
        )

        # 成功的用 OSS URL，失败的降级用原始临时 URL
        oss_urls = []
        fail_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                fail_count += 1
                logger.warning(
                    f"OSS upload failed for url[{i}], using temporary URL | "
                    f"type={task_type} | error={result}"
                )
                oss_urls.append(urls[i])  # 降级使用原始临时 URL
            else:
                oss_urls.append(result)

        if fail_count > 0:
            logger.warning(
                f"Batch OSS upload partial failure | type={task_type} | "
                f"total={len(urls)} | failed={fail_count} | success={len(urls) - fail_count}"
            )

        return oss_urls

    async def _upload_single_to_oss(
        self,
        url: str,
        user_id: str,
        media_type: str,
        max_retries: int = 3,
    ) -> str:
        """
        上传单个 URL 到 OSS，失败抛异常

        Args:
            url: 临时 URL
            user_id: 用户 ID
            media_type: 媒体类型
            max_retries: 最大重试次数

        Returns:
            持久化后的 OSS URL

        Raises:
            ValueError: URL为空或OSS未配置
            Exception: 上传失败
        """
        if not url or not url.strip():
            raise ValueError("Empty URL cannot be uploaded")

        try:
            oss_service = get_oss_service()
        except ValueError as e:
            # OSS 未配置，降级使用原始 URL（已知会过期的风险）
            logger.warning(
                f"OSS not configured, using temporary URL (will expire) | "
                f"error={e}"
            )
            return url

        # 已经是 OSS URL 则跳过
        if oss_service.is_oss_url(url):
            return url

        # 重试上传（带 Full Jitter 退避）
        # 参考：https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
        import asyncio
        import random

        last_error = None
        for attempt in range(max_retries):
            try:
                result = await oss_service.upload_from_url(
                    url=url,
                    user_id=user_id,
                    category="generated",
                    media_type=media_type,
                )

                logger.info(
                    f"OSS upload success | type={media_type} | "
                    f"user_id={user_id} | object_key={result['object_key']} | "
                    f"attempt={attempt + 1}/{max_retries}"
                )
                return result["url"]

            except ValueError as e:
                # ValueError = URL 已失效（403/404/410）或文件过大 → 不可重试
                logger.warning(
                    f"Non-retryable error | type={media_type} | "
                    f"attempt={attempt + 1}/{max_retries} | error={e}"
                )
                raise

            except Exception as e:
                # 其他错误（超时、网络）→ 可重试
                last_error = e
                logger.warning(
                    f"OSS upload attempt {attempt + 1}/{max_retries} failed | "
                    f"type={media_type} | error={e}"
                )

                # 最后一次尝试失败，抛出异常
                if attempt == max_retries - 1:
                    logger.error(
                        f"OSS upload failed after {max_retries} attempts | "
                        f"type={media_type} | user_id={user_id} | error={e}"
                    )
                    raise Exception(
                        f"媒体持久化失败（已重试{max_retries}次）: {e}"
                    ) from last_error

                # Full Jitter 退避：random_between(0, min(cap, base * 2^attempt))
                cap = 16.0
                delay = min(cap, 2.0 ** attempt)
                jitter = random.uniform(0, delay)
                await asyncio.sleep(jitter)

        # 理论上不会到这里（最后一次循环会抛异常）
        raise Exception(f"媒体持久化失败: {last_error}")

    def _build_content_parts(
        self,
        urls: List[str],
        task_type: str,
        task: Dict[str, Any],
    ) -> list:
        """构建 ContentPart 字典列表（含元数据，供 handler.on_complete 使用）"""
        request_params = task.get("request_params") or {}
        parts = []

        for url in urls:
            if not url:
                continue

            if task_type == "image":
                width, height = _compute_image_dimensions(
                    aspect_ratio=request_params.get("aspect_ratio", "1:1"),
                    resolution=request_params.get("resolution"),
                )
                parts.append({
                    "type": "image",
                    "url": url,
                    "width": width,
                    "height": height,
                })

            elif task_type == "video":
                duration = _compute_video_duration(
                    request_params.get("n_frames", "10"),
                )
                parts.append({
                    "type": "video",
                    "url": url,
                    "duration": duration,
                })

        return parts

    def _create_handler(self, task_type: str):
        """根据任务类型创建 Handler"""
        if task_type == "image":
            from services.handlers.image_handler import ImageHandler
            return ImageHandler(self.db)
        elif task_type == "video":
            from services.handlers.video_handler import VideoHandler
            return VideoHandler(self.db)
        else:
            raise ValueError(f"Unknown task type: {task_type}")


def _empty_result(original: TaskResult, fail_code: str, fail_msg: str) -> TaskResult:
    """将成功结果转换为失败结果（用于空结果场景）"""
    if isinstance(original, ImageGenerateResult):
        return ImageGenerateResult(
            task_id=original.task_id,
            status=TaskStatus.FAILED,
            fail_code=fail_code,
            fail_msg=fail_msg,
        )
    else:
        return VideoGenerateResult(
            task_id=original.task_id,
            status=TaskStatus.FAILED,
            fail_code=fail_code,
            fail_msg=fail_msg,
        )
