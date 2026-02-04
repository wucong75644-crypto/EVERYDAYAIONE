"""
后台任务轮询服务

即使用户离线也继续轮询KIE，任务完成后自动保存结果
"""

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from loguru import logger
from supabase import Client

from core.config import Settings, get_settings
from core.task_config import IMAGE_TASK_TIMEOUT_MINUTES, VIDEO_TASK_TIMEOUT_MINUTES
from services.adapters.kie.client import KieClient
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.video_adapter import KieVideoAdapter


class BackgroundTaskWorker:
    """后台任务轮询器（带执行锁防止重叠）"""

    def __init__(self, db: Client):
        self.db = db
        self.settings: Settings = get_settings()
        self.is_running = False
        self._poll_lock = asyncio.Lock()  # 轮询锁（单进程）

    async def start(self):
        """启动后台工作器"""
        self.is_running = True
        logger.info("BackgroundTaskWorker started")

        while self.is_running:
            try:
                # 检查锁，防止轮询重叠
                if self._poll_lock.locked():
                    logger.warning("Previous polling not finished, skipping this round")
                    await asyncio.sleep(30)
                    continue

                async with self._poll_lock:
                    # 1. 轮询进行中的任务
                    await self.poll_pending_tasks()

                    # 2. 清理超时任务
                    await self.cleanup_stale_tasks()

            except Exception as e:
                logger.error(f"BackgroundTaskWorker error: {e}")

            # 等待30秒后继续
            await asyncio.sleep(30)

    async def stop(self):
        """停止后台工作器"""
        self.is_running = False
        logger.info("BackgroundTaskWorker stopped")

    async def poll_pending_tasks(self):
        """轮询所有pending/running任务（带随机抖动）"""
        # 查询所有进行中的任务
        response = self.db.table("tasks").select("*").in_(
            "status", ["pending", "running"]
        ).execute()

        if not response.data:
            return

        logger.debug(f"Polling {len(response.data)} tasks")

        # 随机打散任务（防止惊群效应）
        tasks_shuffled = random.sample(response.data, len(response.data))

        # 动态调整并发数（根据KIE QPS限制）
        kie_qps_limit = getattr(self.settings, 'kie_qps_limit', 50)
        semaphore = asyncio.Semaphore(kie_qps_limit)

        async def process_task_with_jitter(task: dict, index: int):
            # 在30秒窗口内均匀分布（随机抖动）
            jitter_delay = (index / len(tasks_shuffled)) * 30.0
            await asyncio.sleep(jitter_delay)

            async with semaphore:
                try:
                    await self.query_kie_and_update(task)
                except Exception as e:
                    logger.error(
                        f"Failed to process task: {task.get('external_task_id')}, "
                        f"error={e}"
                    )

        await asyncio.gather(*[
            process_task_with_jitter(task, i)
            for i, task in enumerate(tasks_shuffled)
        ])

        logger.info(f"Polled {len(response.data)} tasks in 30s window")

    async def query_kie_and_update(self, task: dict):
        """查询KIE并更新任务状态"""
        external_task_id = task["external_task_id"]
        task_type = task["type"]

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                if task_type == "image":
                    adapter = KieImageAdapter(client, "google/nano-banana")
                else:
                    adapter = KieVideoAdapter(client, "sora-2-text-to-video")

                result = await adapter.query_task(external_task_id)

        except Exception as e:
            logger.error(f"KIE query failed: {external_task_id}, error={e}")
            # 更新 last_polled_at，但不标记为失败（等待下次重试）
            self.db.table("tasks").update({
                "last_polled_at": datetime.now(timezone.utc).isoformat(),
            }).eq("external_task_id", external_task_id).execute()
            return

        # 映射KIE状态
        kie_status = result.get("status")
        status_mapping = {
            "pending": "pending",
            "processing": "running",
            "success": "completed",
            "failed": "failed",
        }
        db_status = status_mapping.get(kie_status, "pending")

        update_data = {
            "status": db_status,
            "last_polled_at": datetime.now(timezone.utc).isoformat(),
        }

        # 任务完成
        if db_status == "completed":
            update_data["result"] = result
            update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
            update_data["credits_used"] = result.get("credits_consumed", 0)

            # 自动创建消息
            if task.get("conversation_id"):
                await self.save_completed_message(task, result)

        # 任务失败
        elif db_status == "failed":
            update_data["fail_code"] = result.get("fail_code")
            update_data["error_message"] = result.get("fail_msg", "任务失败")
            update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

        # 更新数据库
        self.db.table("tasks").update(update_data).eq(
            "external_task_id", external_task_id
        ).execute()

        logger.info(
            f"Task updated: {external_task_id}, status={db_status}, "
            f"type={task_type}"
        )

    async def save_completed_message(self, task: dict, result: dict):
        """任务完成后自动创建消息（带权限验证）"""
        try:
            task_type = task["type"]
            conversation_id = task["conversation_id"]
            task_user_id = task["user_id"]

            # 防御性检查：验证对话所有权（防止恶意用户伪造conversation_id）
            conversation_response = (
                self.db.table("conversations")
                .select("user_id")
                .eq("id", conversation_id)
                .single()
                .execute()
            )

            if not conversation_response.data:
                logger.warning(
                    f"Conversation not found when saving message: "
                    f"conversation_id={conversation_id}, "
                    f"task={task['external_task_id']}"
                )
                return

            conversation_user_id = conversation_response.data["user_id"]

            # 验证任务所有者与对话所有者是否匹配
            if task_user_id != conversation_user_id:
                logger.error(
                    f"Security violation: Task user does not match conversation owner! "
                    f"task={task['external_task_id']}, task_user={task_user_id}, "
                    f"conversation_user={conversation_user_id}, "
                    f"conversation_id={conversation_id}"
                )
                return

            message_data = {
                "conversation_id": conversation_id,
                "content": "生成完成",
                "role": "assistant",
                "credits_cost": task["credits_locked"],
                "generation_params": task["request_params"],
            }

            if task_type == "image":
                image_urls = result.get("image_urls", [])
                if image_urls:
                    message_data["image_url"] = image_urls[0]
            else:
                message_data["video_url"] = result.get("video_url")

            # 创建消息
            self.db.table("messages").insert(message_data).execute()

            # 标记conversation为未读
            self.db.table("conversations").update({
                "unread": True,
            }).eq("id", conversation_id).execute()

            logger.info(
                f"Message created for task: {task['external_task_id']}, "
                f"conversation={conversation_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to save completed message: {task['external_task_id']}, "
                f"error={e}"
            )

    async def cleanup_stale_tasks(self):
        """清理超时任务（包括 chat 类型）"""
        now = datetime.now(timezone.utc)

        # 查询所有pending/running任务
        response = self.db.table("tasks").select("*").in_(
            "status", ["pending", "running"]
        ).execute()

        cleaned_count = 0

        for task in response.data:
            started_at = datetime.fromisoformat(
                task["started_at"].replace("Z", "+00:00")
            )

            # 根据任务类型确定超时时间
            task_type = task["type"]
            if task_type == "chat":
                max_duration_minutes = 10  # chat 任务超时时间：10 分钟
            elif task_type == "image":
                max_duration_minutes = IMAGE_TASK_TIMEOUT_MINUTES
            else:
                max_duration_minutes = VIDEO_TASK_TIMEOUT_MINUTES

            # 检查是否超时
            if (now - started_at).total_seconds() > max_duration_minutes * 60:
                # 标记为失败
                self.db.table("tasks").update({
                    "status": "failed",
                    "error_message": f"任务超时 (超过{max_duration_minutes}分钟)",
                    "completed_at": now.isoformat(),
                }).eq("id", task["id"]).execute()

                cleaned_count += 1
                logger.warning(
                    f"Task timeout: id={task['id']}, "
                    f"external_id={task.get('external_task_id')}, "
                    f"type={task_type}"
                )

        if cleaned_count > 0:
            logger.info(f"Cleaned {cleaned_count} stale tasks")
