"""
TaskMixin - 任务状态管理

提供任务 CRUD 和状态更新功能：
- 获取任务
- 完成任务（带乐观锁）
- 失败任务（带乐观锁）
"""

from typing import Any, Dict, Optional
from datetime import datetime

from loguru import logger


class TaskMixin:
    """
    任务管理 Mixin

    提供任务状态管理的基础方法，支持：
    - 任务查询
    - 任务完成（带幂等性检查和乐观锁）
    - 任务失败（带幂等性检查和乐观锁）
    """

    def _get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        result = (
            self.db.table("tasks")
            .select("*")
            .eq("external_task_id", task_id)
            .maybe_single()
            .execute()
        )
        return result.data if (result and result.data) else None

    def _complete_task(self, task_id: str, task: Optional[Dict[str, Any]] = None) -> None:
        """
        标记任务完成

        两种调用路径：
        1. Chat 任务直接调用：需要更新 version + started_at + status
        2. Image/Video 通过 process_result 调用：只更新 status（version 已由 process_result 更新）

        判断依据：started_at 是否已设置
        - 已设置 → process_result 路径 → 只更新 status
        - 未设置 → Chat 直接路径 → 更新 version + started_at + status

        Args:
            task_id: 外部任务 ID
            task: 已查询的任务数据（可选，传入时跳过 DB 查询）
        """
        if task is None:
            task_result = self.db.table("tasks").select("version, started_at, status").eq("external_task_id", task_id).execute()
            if not task_result.data:
                logger.error(f"Task not found for completion | task_id={task_id}")
                return
            task = task_result.data[0]

        # 幂等性检查：如果已经是终态，跳过
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.debug(f"Task already in terminal state | task_id={task_id} | status={task['status']}")
            return

        current_version = task.get("version", 1)

        # 路径1：started_at 已设置 → Image/Video 通过 process_result 调用
        # process_result 已经更新了 version，这里只更新 status 和 completed_at
        if task.get("started_at"):
            self.db.table("tasks").update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("external_task_id", task_id).execute()

            logger.debug(
                f"Task completed (process_result path) | "
                f"task_id={task_id} | version={current_version} (unchanged)"
            )

        # 路径2：started_at 未设置 → Chat 直接调用
        # 需要更新 version + started_at + status
        else:
            update_data = {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "version": current_version + 1,
                "started_at": datetime.utcnow().isoformat(),
            }

            # 执行更新（带乐观锁检查）
            result = self.db.table("tasks").update(update_data).eq("external_task_id", task_id).eq("version", current_version).execute()

            if not result.data:
                logger.warning(
                    f"Task completion lock failed (concurrent update) | "
                    f"task_id={task_id} | version={current_version}"
                )
            else:
                logger.debug(
                    f"Task completed (chat path) | "
                    f"task_id={task_id} | version={current_version}→{current_version + 1}"
                )

    def _fail_task(self, task_id: str, error_message: str, task: Optional[Dict[str, Any]] = None) -> None:
        """
        标记任务失败

        同 _complete_task 逻辑：根据 started_at 判断调用路径

        Args:
            task_id: 外部任务 ID
            error_message: 错误信息
            task: 已查询的任务数据（可选，传入时跳过 DB 查询）
        """
        if task is None:
            task_result = self.db.table("tasks").select("version, started_at, status").eq("external_task_id", task_id).execute()
            if not task_result.data:
                logger.error(f"Task not found for failure | task_id={task_id}")
                return
            task = task_result.data[0]

        # 幂等性检查
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.debug(f"Task already in terminal state | task_id={task_id} | status={task['status']}")
            return

        current_version = task.get("version", 1)

        # 路径1：started_at 已设置 → process_result 路径
        if task.get("started_at"):
            self.db.table("tasks").update({
                "status": "failed",
                "error_message": error_message,
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("external_task_id", task_id).execute()

            logger.debug(
                f"Task failed (process_result path) | "
                f"task_id={task_id} | version={current_version} (unchanged)"
            )

        # 路径2：started_at 未设置 → Chat 路径
        else:
            update_data = {
                "status": "failed",
                "error_message": error_message,
                "completed_at": datetime.utcnow().isoformat(),
                "version": current_version + 1,
                "started_at": datetime.utcnow().isoformat(),
            }

            result = self.db.table("tasks").update(update_data).eq("external_task_id", task_id).eq("version", current_version).execute()

            if not result.data:
                logger.warning(
                    f"Task failure lock failed (concurrent update) | "
                    f"task_id={task_id} | version={current_version}"
                )
            else:
                logger.debug(
                    f"Task failed (chat path) | "
                    f"task_id={task_id} | version={current_version}→{current_version + 1}"
                )
