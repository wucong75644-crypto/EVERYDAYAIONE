"""
统一任务完成处理服务

Webhook 和轮询兜底的统一入口，保证：
1. 幂等性：已完成的任务不重复处理
2. 格式一致：统一走 handler.on_complete/on_error
3. OSS 上传：在调用 handler 前完成（临时 URL → 持久化）
"""

import asyncio
from contextlib import suppress
from typing import Dict, Any, List, Optional, Union

from loguru import logger


from services.adapters.base import (
    ImageGenerateResult,
    VideoGenerateResult,
    TaskStatus,
)
from services.assets.asset_registry import register_task_media_best_effort
from services.media_result_persistence import (
    MediaResultPersistence,
    compute_image_dimensions as _compute_image_dimensions,
    compute_video_duration as _compute_video_duration,
)
from services.worker_media_tasks import WorkerMediaTasks

TaskResult = Union[ImageGenerateResult, VideoGenerateResult]

_COMPLETION_LOCK_TTL_SECONDS = 300
_COMPLETION_LOCK_RENEW_SECONDS = 60


class TaskCompletionService(MediaResultPersistence):
    """
    统一任务完成处理入口

    接收标准 ImageGenerateResult / VideoGenerateResult，
    不关心结果来自 Webhook 还是轮询、来自哪个 Provider。
    """

    def __init__(self, db):
        self.db = db
        self._media_tasks = WorkerMediaTasks(db)

    def get_task(self, external_task_id: str) -> Optional[Dict[str, Any]]:
        """根据 external_task_id 查询任务"""
        return self._media_tasks.get(external_task_id)

    async def process_result(self, external_task_id: str, result: TaskResult) -> bool:
        """
        统一处理入口（Redis 分布式锁 + DB 乐观锁）

        Redis 锁覆盖整个媒体持久化和积分结算过程，防止 Webhook
        与轮询错时进入；DB version 检查保留为第二层幂等保护。

        Args:
            external_task_id: 外部任务 ID
            result: 统一结果（ImageGenerateResult 或 VideoGenerateResult）

        Returns:
            True = 已处理（含幂等跳过），False = 处理失败
        """
        if result.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            return True

        # 终态和不存在的任务无需依赖 Redis，保持原有幂等语义。
        existing_task = self.get_task(external_task_id)
        if not existing_task:
            logger.warning(f"Task not found | task_id={external_task_id}")
            return False
        if existing_task["status"] in ("completed", "failed", "cancelled"):
            logger.info(
                f"Task already {existing_task['status']}, skipping | "
                f"task_id={external_task_id}"
            )
            from services.task_limit_service import release_task_slot
            await release_task_slot(existing_task)
            return True

        from core.redis import RedisClient

        lock_key = f"task_completion:{external_task_id}"
        try:
            lock_token = await RedisClient.acquire_lock(
                lock_key,
                timeout=_COMPLETION_LOCK_TTL_SECONDS,
            )
        except Exception as e:
            logger.error(
                f"Task completion lock unavailable | task_id={external_task_id} | "
                f"error={e}"
            )
            return False

        if not lock_token:
            logger.info(
                f"Task completion already in progress | task_id={external_task_id}"
            )
            return True

        renewal_task = asyncio.create_task(
            self._renew_completion_lock(lock_key, lock_token),
            name=f"completion-lock:{external_task_id}",
        )
        try:
            return await self._process_result_locked(external_task_id, result)
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await renewal_task
            try:
                await RedisClient.release_lock(lock_key, lock_token)
            except Exception as e:
                logger.error(
                    f"Task completion lock release failed | "
                    f"task_id={external_task_id} | error={e}"
                )

    async def _renew_completion_lock(self, lock_key: str, lock_token: str) -> None:
        """定期续期处理锁，覆盖大图下载和存储重试。"""
        from core.redis import RedisClient

        while True:
            await asyncio.sleep(_COMPLETION_LOCK_RENEW_SECONDS)
            try:
                extended = await RedisClient.extend_lock(
                    lock_key,
                    lock_token,
                    timeout=_COMPLETION_LOCK_TTL_SECONDS,
                )
            except Exception as e:
                logger.error(
                    f"Task completion lock renewal failed | "
                    f"key={lock_key} | error={e}"
                )
                return
            if not extended:
                logger.error(f"Task completion lock lost | key={lock_key}")
                return

    async def _process_result_locked(
        self,
        external_task_id: str,
        result: TaskResult,
    ) -> bool:
        """持有分布式锁时执行原有完成处理。"""
        # pending/processing 状态忽略（轮询场景，任务仍在进行中）
        if result.status not in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            return True

        # 1. 查询当前任务状态（获取version用于乐观锁）
        task = self.get_task(external_task_id)
        if not task:
            logger.warning(f"Task not found | task_id={external_task_id}")
            return False

        delivery_context = task.get("delivery_context")
        if isinstance(delivery_context, dict) and delivery_context.get("runtime") is True:
            logger.info(
                "Skipping Runtime-owned task in legacy completion service | "
                f"task_id={external_task_id}"
            )
            return True

        # 2. 幂等检查：任务已经是终态，跳过处理
        if task['status'] in ['completed', 'failed', 'cancelled']:
            logger.info(
                f"Task already {task['status']}, skipping | "
                f"task_id={external_task_id}"
            )
            # 兜底释放 Redis 槽位（SREM 幂等）：
            # 覆盖 cancel/fail 路径 release 前的遗留 + 防御未来新增直接改 status 的路径
            from services.task_limit_service import release_task_slot
            await release_task_slot(task)
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
        claimed_task = self._media_tasks.claim_completion(
            external_task_id,
            current_version,
        )

        # 5. 检查是否抢到锁
        if not claimed_task:
            logger.info(
                f"Task lock failed (concurrent processing) | task_id={external_task_id}"
            )
            return True  # 其他进程已处理，幂等返回成功

        # 6. 使用原子领取返回的完整任务快照。
        task = claimed_task

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

        # 2. OSS 上传 + 构建 ContentPart
        # image 走 persist_media_urls_to_workspace(下载→NAS→OSS+workspace_path 一站式);
        # video 沿用 _upload_urls_to_oss(只上 OSS,不进工作区)
        org_id = task.get("org_id")
        if task_type == "image":
            content_parts = await self._build_content_parts(raw_urls, task_type, task)
        else:
            oss_urls = await self._upload_urls_to_oss(raw_urls, user_id, task_type, org_id=org_id)
            content_parts = await self._build_content_parts(oss_urls, task_type, task)

        # 4. 空结果检查
        if not content_parts:
            logger.warning(
                f"No result content | task_id={external_task_id} | "
                f"raw_urls={raw_urls}"
            )
            return await self._handle_failure(task, _empty_result(
                result, "NO_RESULT", "生成结果为空",
            ))

        register_task_media_best_effort(
            self.db,
            task=task,
            content_parts=content_parts,
        )

        # 5. 图片任务统一走批次处理（含 num_images=1）
        if task_type == "image" and task.get("batch_id"):
            from services.batch_completion_service import BatchCompletionService
            batch_svc = BatchCompletionService(self.db)
            return await batch_svc.handle_image_complete(task, content_parts)

        # 6. 其他任务（video）走原有 Handler 路径
        handler = self._create_handler(task_type, org_id=task.get("org_id"))
        if task_type == "video":
            handler.worker_task_context = task
        await handler.on_complete(
            task_id=external_task_id,
            result=content_parts,
        )

        logger.info(
            f"Task completed via unified service | task_id={external_task_id} | "
            f"type={task_type} | urls={len(content_parts)}"
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
        handler = self._create_handler(task_type, org_id=task.get("org_id"))
        if task_type == "video":
            handler.worker_task_context = task
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

    def _create_handler(self, task_type: str, org_id: str | None = None):
        """根据任务类型创建 Handler"""
        if task_type == "image":
            from services.handlers.image_handler import ImageHandler
            h = ImageHandler(self.db)
        elif task_type == "video":
            from services.handlers.video_handler import VideoHandler
            h = VideoHandler(self.db)
        else:
            raise ValueError(f"Unknown task type: {task_type}")
        h.org_id = org_id
        return h


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
