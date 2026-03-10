"""
后台任务轮询服务

运行模式由环境变量自动决定：
- 有 CALLBACK_BASE_URL → 兜底模式（默认 120s）
- 无 CALLBACK_BASE_URL → 主轮询模式（默认 15s）
- 可通过 POLL_INTERVAL_SECONDS 手动覆盖

统一调用 TaskCompletionService 处理完成/失败。
超时清理走 handler.on_error()，确保积分退回 + WebSocket 推送。
"""

import asyncio
import random
from datetime import datetime, timezone

from loguru import logger
from supabase import Client

from core.config import Settings, get_settings
from core.task_config import IMAGE_TASK_TIMEOUT_MINUTES, VIDEO_TASK_TIMEOUT_MINUTES
from services.adapters import (
    create_image_adapter,
    create_video_adapter,
)
from services.adapters.base import (
    ImageGenerateResult,
    VideoGenerateResult,
    TaskStatus,
)
from services.task_completion_service import TaskCompletionService

# 默认轮询间隔（秒）
_DEFAULT_POLL_INTERVAL_WITH_WEBHOOK = 120  # 有回调时：兜底模式
_DEFAULT_POLL_INTERVAL_NO_WEBHOOK = 15     # 无回调时：主轮询模式


def _resolve_poll_interval(settings: Settings) -> int:
    """根据配置自动选择轮询间隔"""
    if settings.poll_interval_seconds > 0:
        return settings.poll_interval_seconds
    if settings.callback_base_url:
        return _DEFAULT_POLL_INTERVAL_WITH_WEBHOOK
    return _DEFAULT_POLL_INTERVAL_NO_WEBHOOK


class BackgroundTaskWorker:
    """后台任务轮询器（自适应模式，带执行锁防止重叠）"""

    def __init__(self, db: Client):
        self.db = db
        self.settings: Settings = get_settings()
        self.poll_interval = _resolve_poll_interval(self.settings)
        self.is_running = False
        self._poll_lock = asyncio.Lock()
        self._last_consistency_check = None  # 上次数据一致性检查时间
        self._last_scoring_aggregation = None  # 上次模型评分聚合时间

    async def start(self):
        """启动后台工作器"""
        self.is_running = True
        mode = "fallback" if self.settings.callback_base_url else "primary"
        logger.info(
            f"BackgroundTaskWorker started | mode={mode} | "
            f"interval={self.poll_interval}s | "
            f"callback={'configured' if self.settings.callback_base_url else 'none'}"
        )

        while self.is_running:
            try:
                if self._poll_lock.locked():
                    logger.warning("Previous polling not finished, skipping this round")
                    await asyncio.sleep(self.poll_interval)
                    continue

                async with self._poll_lock:
                    await self.poll_pending_tasks()
                    await self.cleanup_stale_tasks()
                    await self.check_data_consistency()
                    await self._run_model_scoring()

            except Exception as e:
                logger.error(f"BackgroundTaskWorker error: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        """停止后台工作器"""
        self.is_running = False
        logger.info("BackgroundTaskWorker stopped")

    async def poll_pending_tasks(self):
        """
        轮询所有 pending/running 的 image/video 任务

        Chat 任务由流式处理管理，不参与轮询。
        使用随机抖动避免惊群效应。
        """
        try:
            response = self.db.table("tasks").select("*").in_(
                "status", ["pending", "running"]
            ).in_(
                "type", ["image", "video"]
            ).execute()
        except Exception as e:
            logger.warning(f"Failed to query pending tasks (DB connection error) | error={e}")
            return

        if not response or not response.data:
            return

        logger.debug(f"Polling {len(response.data)} tasks (fallback)")

        tasks_shuffled = random.sample(response.data, len(response.data))
        kie_qps_limit = getattr(self.settings, 'kie_qps_limit', 50)
        semaphore = asyncio.Semaphore(kie_qps_limit)

        async def process_task_with_jitter(task: dict, index: int):
            # 在 60 秒窗口内均匀分布
            jitter_delay = (index / len(tasks_shuffled)) * 60.0
            await asyncio.sleep(jitter_delay)

            async with semaphore:
                try:
                    await self.query_and_process(task)
                except Exception as e:
                    logger.error(
                        f"Failed to process task | "
                        f"task_id={task.get('external_task_id')} | error={e}",
                        exc_info=True
                    )

        await asyncio.gather(*[
            process_task_with_jitter(task, i)
            for i, task in enumerate(tasks_shuffled)
        ])

        logger.info(f"Polled {len(response.data)} tasks (fallback)")

    async def query_and_process(self, task: dict):
        """
        查询 Provider 任务状态，完成/失败时交给统一处理服务

        使用任务记录中的 model_id 创建适配器（而非硬编码）。
        """
        external_task_id = task["external_task_id"]
        task_type = task["type"]
        model_id = task.get("model_id")

        # 🔥 DEBUG: 记录开始查询
        logger.info(
            f"[POLL] Querying task | task_id={external_task_id} | "
            f"type={task_type} | model={model_id}"
        )

        try:
            if task_type == "image":
                adapter = create_image_adapter(model_id)
            else:
                adapter = create_video_adapter(model_id)

            try:
                query_result = await adapter.query_task(external_task_id)

                # 🔥 DEBUG: 记录查询结果
                logger.info(
                    f"[POLL] Query result | task_id={external_task_id} | "
                    f"status={query_result.status.value} | "
                    f"has_urls={'Yes' if (hasattr(query_result, 'image_urls') and query_result.image_urls) or (hasattr(query_result, 'video_url') and query_result.video_url) else 'No'}"
                )
            finally:
                await adapter.close()

        except Exception as e:
            logger.error(
                f"[POLL] Provider query failed | task_id={external_task_id} | "
                f"model={model_id} | error={e}",
                exc_info=True
            )
            self.db.table("tasks").update({
                "last_polled_at": datetime.now(timezone.utc).isoformat(),
            }).eq("external_task_id", external_task_id).execute()
            return

        # 更新 last_polled_at 用于监控
        self.db.table("tasks").update({
            "last_polled_at": datetime.now(timezone.utc).isoformat(),
        }).eq("external_task_id", external_task_id).execute()

        # 完成/失败 → 交给统一处理服务（包含幂等检查）
        if query_result.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
            print(f"🔥🔥🔥 POLL: Task ready | {external_task_id} | {query_result.status.value}", flush=True)
            logger.info(
                f"[POLL] Task ready for processing | task_id={external_task_id} | "
                f"status={query_result.status.value}"
            )

            service = TaskCompletionService(self.db)
            print(f"🔥🔥🔥 POLL: Calling process_result | {external_task_id}", flush=True)
            await service.process_result(external_task_id, query_result)
            print(f"🔥🔥🔥 POLL: process_result done | {external_task_id}", flush=True)

            logger.info(
                f"Task processed via fallback | task_id={external_task_id} | "
                f"status={query_result.status.value}"
            )
        else:
            # 🔥 DEBUG: 记录非终态任务
            logger.info(
                f"[POLL] Task not ready | task_id={external_task_id} | "
                f"status={query_result.status.value} | skipping"
            )

    async def cleanup_stale_tasks(self):
        """清理超时任务（包括 chat 类型）"""
        now = datetime.now(timezone.utc)

        try:
            response = self.db.table("tasks").select("*").in_(
                "status", ["pending", "running"]
            ).execute()
        except Exception as e:
            logger.warning(f"Failed to query stale tasks (DB connection error) | error={e}")
            return

        if not response or not response.data:
            return

        cleaned_count = 0

        for task in response.data:
            # 跳过没有 started_at 的任务（可能是旧数据或创建时未设置）
            if not task.get("started_at"):
                continue

            started_at = datetime.fromisoformat(
                task["started_at"].replace("Z", "+00:00")
            )

            task_type = task["type"]
            if task_type == "chat":
                max_duration_minutes = 10
            elif task_type == "image":
                max_duration_minutes = IMAGE_TASK_TIMEOUT_MINUTES
            else:
                max_duration_minutes = VIDEO_TASK_TIMEOUT_MINUTES

            if (now - started_at).total_seconds() > max_duration_minutes * 60:
                await self._handle_timeout(task, max_duration_minutes)
                cleaned_count += 1

        if cleaned_count > 0:
            logger.info(f"Cleaned {cleaned_count} stale tasks")

    async def _handle_timeout(self, task: dict, max_duration_minutes: int):
        """
        处理超时任务

        image/video：通过 TaskCompletionService 统一处理（退回积分+更新消息+WebSocket 推送）
        chat：直接标记失败 + 退回积分（chat 有自己的流式错误处理机制）
        """
        external_task_id = task.get("external_task_id", "unknown")
        task_type = task["type"]
        error_msg = f"任务超时 (超过{max_duration_minutes}分钟)"

        # image/video 通过统一处理服务
        if task_type in ("image", "video") and external_task_id != "unknown":
            try:
                if task_type == "image":
                    timeout_result = ImageGenerateResult(
                        task_id=external_task_id,
                        status=TaskStatus.FAILED,
                        fail_code="TIMEOUT",
                        fail_msg=error_msg,
                    )
                else:
                    timeout_result = VideoGenerateResult(
                        task_id=external_task_id,
                        status=TaskStatus.FAILED,
                        fail_code="TIMEOUT",
                        fail_msg=error_msg,
                    )

                service = TaskCompletionService(self.db)
                success = await service.process_result(external_task_id, timeout_result)

                if success:
                    logger.warning(
                        f"Task timeout | task_id={external_task_id} | "
                        f"type={task_type} | limit={max_duration_minutes}min"
                    )
                    return

                logger.warning(
                    f"Timeout via service returned False, falling back | "
                    f"task_id={external_task_id}"
                )
            except Exception as e:
                logger.error(
                    f"Timeout via service failed, falling back | "
                    f"task_id={external_task_id} | error={e}"
                )

        # Chat 任务或 fallback：直接更新数据库 + 退回积分
        # 注：_refund_credits 检查 status="pending"，不会重复退回
        try:
            transaction_id = task.get("credit_transaction_id")
            if transaction_id:
                await self._refund_credits(transaction_id)

            self.db.table("tasks").update({
                "status": "failed",
                "error_message": error_msg,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", task["id"]).execute()

            logger.warning(
                f"Task timeout (direct) | id={task['id']} | "
                f"external_id={external_task_id} | "
                f"type={task_type} | refunded={bool(transaction_id)}"
            )
        except Exception as e:
            logger.error(
                f"Timeout direct fallback failed | id={task['id']} | "
                f"task_id={external_task_id} | error={e}"
            )

    async def _refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（chat 任务超时时调用）

        image/video 超时走 TaskCompletionService → handler.on_error() 自动退回
        """
        try:
            tx_result = self.db.table("credit_transactions").select("*").eq(
                "id", transaction_id
            ).maybe_single().execute()

            if not tx_result.data:
                logger.warning(f"Refund failed: transaction not found | id={transaction_id}")
                return

            tx = tx_result.data
            if tx["status"] != "pending":
                logger.warning(
                    f"Refund skipped: status={tx['status']} | id={transaction_id}"
                )
                return

            self.db.rpc(
                'refund_credits',
                {
                    'p_user_id': tx["user_id"],
                    'p_amount': tx["amount"]
                }
            ).execute()

            self.db.table("credit_transactions").update({
                "status": "refunded",
                "confirmed_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", transaction_id).execute()

            logger.info(
                f"Credits refunded | transaction_id={transaction_id} | "
                f"user_id={tx['user_id']} | amount={tx['amount']}"
            )

        except Exception as e:
            logger.error(f"Refund failed | transaction_id={transaction_id} | error={e}")

    async def check_data_consistency(self):
        """
        定期检查数据一致性（每小时运行一次）

        检查以下异常并发送告警（不自动修复）：
        - completed 消息但没有 URL
        - pending 消息但有 URL
        - 超过24小时的 pending 消息
        """
        from datetime import datetime, timezone
        from services.data_consistency_checker import DataConsistencyChecker

        now = datetime.now(timezone.utc)

        # 检查是否需要运行（每小时一次）
        if self._last_consistency_check is not None:
            elapsed = (now - self._last_consistency_check).total_seconds()
            if elapsed < 3600:  # 1小时 = 3600秒
                return

        try:
            checker = DataConsistencyChecker(self.db)
            results = await checker.check_and_alert()  # 🔥 改为只告警

            self._last_consistency_check = now

            # 记录检查结果
            # 详细日志已在 checker._send_alert() 中记录
            # 这里只记录简要信息
            if results["total_issues"] == 0:
                logger.debug("Data consistency check completed | no issues found")

        except Exception as e:
            logger.error(f"Data consistency check failed | error={e}", exc_info=True)

    async def _run_model_scoring(self):
        """每小时执行模型评分聚合"""
        now = datetime.now(timezone.utc)
        if self._last_scoring_aggregation is not None:
            elapsed = (now - self._last_scoring_aggregation).total_seconds()
            if elapsed < 3600:
                return

        try:
            from services.model_scorer import aggregate_model_scores

            await aggregate_model_scores()
        except Exception as e:
            logger.error(f"Model scoring aggregation failed | error={e}")
        finally:
            self._last_scoring_aggregation = now
