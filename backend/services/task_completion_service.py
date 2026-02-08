"""
统一任务完成处理服务

Webhook 和轮询兜底的统一入口，保证：
1. 幂等性：已完成的任务不重复处理
2. 格式一致：统一走 handler.on_complete/on_error
3. OSS 上传：在调用 handler 前完成（临时 URL → 持久化）
"""

from typing import Dict, Any, List, Optional, Union

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
        统一处理入口（乐观锁防并发）

        通过 UPDATE ... WHERE status IN ('pending','running') 原子抢占，
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

        # 1. 乐观锁：原子抢占（只有 pending/running 状态才能被处理）
        lock_result = (
            self.db.table("tasks")
            .update({"status": "processing"})
            .eq("external_task_id", external_task_id)
            .in_("status", ["pending", "running"])
            .execute()
        )

        if not lock_result.data:
            # 抢占失败：任务已被其他 Webhook/轮询处理，或不存在
            task = self.get_task(external_task_id)
            if not task:
                logger.warning(f"Task not found | task_id={external_task_id}")
                return False
            logger.info(
                f"Task already {task['status']}, skipping | "
                f"task_id={external_task_id}"
            )
            return True

        task = lock_result.data[0]

        # 2. 根据结果状态分发
        try:
            if result.status == TaskStatus.SUCCESS:
                return await self._handle_success(task, result)
            else:
                return await self._handle_failure(task, result)
        except Exception as e:
            # 处理失败：回退状态为 running，让轮询兜底重试
            logger.error(
                f"Task completion failed, reverting to running | "
                f"task_id={external_task_id} | error={e}"
            )
            self.db.table("tasks").update(
                {"status": "running"}
            ).eq("external_task_id", external_task_id).eq(
                "status", "processing"
            ).execute()
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

        # 3. 构建 ContentPart 列表
        content_parts = self._build_content_parts(oss_urls, task_type)

        # 4. 空结果检查
        if not content_parts:
            logger.warning(
                f"No result content | task_id={external_task_id} | "
                f"raw_urls={raw_urls}"
            )
            return await self._handle_failure(task, _empty_result(
                result, "NO_RESULT", "生成结果为空",
            ))

        # 5. 调用 Handler.on_complete
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
        """处理失败结果"""
        external_task_id = task["external_task_id"]
        task_type = task["type"]

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
        """从统一结果中提取媒体 URL 列表"""
        if task_type == "image" and isinstance(result, ImageGenerateResult):
            return result.image_urls or []
        elif task_type == "video" and isinstance(result, VideoGenerateResult):
            return [result.video_url] if result.video_url else []
        return []

    async def _upload_urls_to_oss(
        self,
        urls: List[str],
        user_id: str,
        task_type: str,
    ) -> List[str]:
        """
        批量上传媒体到 OSS

        KIE 等 Provider 返回的临时 URL 会过期，需上传到 OSS 持久化。
        上传失败时返回原 URL（降级处理）。
        """
        if not urls:
            return []

        oss_urls = []
        for url in urls:
            oss_url = await self._upload_single_to_oss(url, user_id, task_type)
            oss_urls.append(oss_url)

        return oss_urls

    async def _upload_single_to_oss(
        self,
        url: str,
        user_id: str,
        media_type: str,
    ) -> str:
        """上传单个 URL 到 OSS，失败返回原 URL"""
        if not url:
            return url

        try:
            oss_service = get_oss_service()

            # 已经是 OSS URL 则跳过
            if oss_service.is_oss_url(url):
                return url

            result = await oss_service.upload_from_url(
                url=url,
                user_id=user_id,
                category="generated",
                media_type=media_type,
            )

            logger.info(
                f"OSS upload success | type={media_type} | "
                f"user_id={user_id} | object_key={result['object_key']}"
            )
            return result["url"]

        except ValueError as e:
            logger.warning(f"OSS not configured, using original URL | error={e}")
            return url
        except Exception as e:
            logger.error(f"OSS upload failed, using original URL | error={e}")
            return url

    def _build_content_parts(self, urls: List[str], task_type: str) -> list:
        """构建 ContentPart 字典列表（供 handler.on_complete 使用）"""
        parts = []
        for url in urls:
            if not url:
                continue
            if task_type == "image":
                parts.append({"type": "image", "url": url})
            elif task_type == "video":
                parts.append({"type": "video", "url": url})
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
