"""
多图批次完成处理服务

从 TaskCompletionService 提取的图片批次逻辑：
- 单个 task 完成/失败 → 确认/退回积分 → 推送 partial update
- 全部终态 → finalize batch → upsert message → 推送 message_done

设计原则：
- 每个 task 独立处理积分和状态
- _finalize_batch() 通过 message status 乐观锁防并发
- 所有图片任务（含 num_images=1）统一走此路径
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from loguru import logger


from schemas.websocket import (
    build_image_partial_update,
    build_message_done,
)
from services.websocket_manager import ws_manager


class BatchCompletionService:
    """多图批次完成处理"""

    def __init__(self, db):
        self.db = db

    async def handle_image_complete(
        self,
        task: Dict[str, Any],
        content_parts: List[Dict[str, Any]],
    ) -> bool:
        """
        处理单个图片 task 成功

        Args:
            task: 完整 task 行数据
            content_parts: OSS 上传后的 ContentPart 字典列表（通常 1 个）

        Returns:
            True = 处理成功
        """
        ext_task_id = task["external_task_id"]
        batch_id = task["batch_id"]
        image_index = task.get("image_index", 0)

        # 1. 确认积分扣除
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._confirm_credits(transaction_id)

        # 2. 存储 result_data + 标记 task completed
        result_data = content_parts[0] if content_parts else None
        self.db.table("tasks").update({
            "status": "completed",
            "result_data": result_data,
        }).eq("external_task_id", ext_task_id).execute()

        # 3. 查询批次进度
        batch_tasks = self._get_batch_tasks(batch_id)
        completed_count, total_count = self._count_terminal(batch_tasks)

        # 4. 推送 image_partial_update
        await self._push_partial_update(
            task=task,
            image_index=image_index,
            content_part=result_data,
            completed_count=completed_count,
            total_count=total_count,
        )

        # 5. 全部终态 → finalize（区分 regenerate_single 和批次生成）
        if completed_count >= total_count:
            await self._dispatch_finalize(batch_id, batch_tasks)

        logger.info(
            f"Batch task completed | task_id={ext_task_id} | "
            f"index={image_index} | progress={completed_count}/{total_count}"
        )
        return True

    async def handle_image_failure(
        self,
        task: Dict[str, Any],
        error_code: str,
        error_message: str,
    ) -> bool:
        """
        处理单个图片 task 失败

        Args:
            task: 完整 task 行数据
            error_code: 错误代码
            error_message: 错误消息

        Returns:
            True = 处理成功
        """
        ext_task_id = task["external_task_id"]
        batch_id = task["batch_id"]
        image_index = task.get("image_index", 0)

        # 1. 退回积分
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            try:
                self._refund_credits(transaction_id)
            except Exception as refund_err:
                logger.critical(
                    f"Batch image refund failed | ext_task_id={ext_task_id} | "
                    f"tx={transaction_id} | error={refund_err}"
                )

        # 2. 标记 task failed
        self.db.table("tasks").update({
            "status": "failed",
            "error_message": error_message,
        }).eq("external_task_id", ext_task_id).execute()

        # 3. 查询批次进度
        batch_tasks = self._get_batch_tasks(batch_id)
        completed_count, total_count = self._count_terminal(batch_tasks)

        # 4. 推送 image_partial_update（error）
        await self._push_partial_update(
            task=task,
            image_index=image_index,
            content_part=None,
            completed_count=completed_count,
            total_count=total_count,
            error=error_message,
        )

        # 5. 全部终态 → finalize（区分 regenerate_single 和批次生成）
        if completed_count >= total_count:
            await self._dispatch_finalize(batch_id, batch_tasks)

        logger.info(
            f"Batch task failed | task_id={ext_task_id} | "
            f"index={image_index} | error={error_code} | "
            f"progress={completed_count}/{total_count}"
        )
        return True

    # ========================================
    # 内部方法
    # ========================================

    def _get_batch_tasks(self, batch_id: str) -> List[Dict[str, Any]]:
        """查询同 batch_id 的所有 tasks"""
        result = (
            self.db.table("tasks")
            .select("*")
            .eq("batch_id", batch_id)
            .order("image_index")
            .execute()
        )
        return result.data or []

    def _count_terminal(self, batch_tasks: List[Dict[str, Any]]) -> tuple:
        """统计终态数量，返回 (terminal_count, total_count)"""
        total = len(batch_tasks)
        terminal = sum(
            1 for t in batch_tasks
            if t.get("status") in ("completed", "failed", "cancelled")
        )
        return terminal, total

    async def _push_partial_update(
        self,
        task: Dict[str, Any],
        image_index: int,
        content_part: Any,
        completed_count: int,
        total_count: int,
        error: str = None,
    ) -> None:
        """推送 image_partial_update WebSocket 事件"""
        client_task_id = task.get("client_task_id")
        user_id = task["user_id"]
        conversation_id = task["conversation_id"]
        message_id = task["placeholder_message_id"]

        msg = build_image_partial_update(
            task_id=client_task_id or task["external_task_id"],
            conversation_id=conversation_id,
            message_id=message_id,
            image_index=image_index,
            content_part=content_part,
            completed_count=completed_count,
            total_count=total_count,
            error=error,
        )

        # 优先推送到 task 订阅者，fallback 到 user
        await ws_manager.send_to_task_or_user(
            task_id=client_task_id or task["external_task_id"],
            user_id=user_id,
            message=msg,
        )

    async def _dispatch_finalize(
        self,
        batch_id: str,
        batch_tasks: List[Dict[str, Any]],
    ) -> None:
        """根据操作类型分发到对应的 finalize 方法"""
        if not batch_tasks:
            logger.warning(f"dispatch_finalize called with empty batch | batch_id={batch_id}")
            return

        first_task = batch_tasks[0]
        request_params = first_task.get("request_params") or {}
        if isinstance(request_params, str):
            request_params = json.loads(request_params)
        operation = request_params.get("operation")

        if operation == "regenerate_single":
            await self._finalize_single_image(batch_id, batch_tasks)
        else:
            await self._finalize_batch(batch_id, batch_tasks)

    async def _finalize_single_image(
        self,
        batch_id: str,
        batch_tasks: List[Dict[str, Any]],
    ) -> None:
        """
        单图重新生成的最终处理（merge-update）

        与 _finalize_batch（全量替换）不同，此方法：
        1. 读取现有消息的 content 数组
        2. 仅更新 content[image_index] 位置
        3. 保持其他图片不变
        """
        if not batch_tasks:
            return

        the_task = batch_tasks[0]
        message_id = the_task["placeholder_message_id"]
        image_index = the_task.get("image_index", 0)
        client_task_id = the_task.get("client_task_id")
        user_id = the_task["user_id"]
        conversation_id = the_task["conversation_id"]

        try:
            # 1. 读取现有消息
            msg_result = (
                self.db.table("messages")
                .select("content, credits_cost, generation_params, created_at")
                .eq("id", message_id)
                .single()
                .execute()
            )
            if not msg_result.data:
                raise ValueError(
                    f"Message not found for single image finalize | "
                    f"message_id={message_id}"
                )

            content = msg_result.data.get("content", [])
            if isinstance(content, str):
                content = json.loads(content)
            current_credits = msg_result.data.get("credits_cost", 0)

            # 2. 更新 content[image_index]
            # 确保 content 数组长度足够
            while len(content) <= image_index:
                content.append({"type": "image", "url": None})

            if the_task["status"] == "completed" and the_task.get("result_data"):
                content[image_index] = the_task["result_data"]
                current_credits += the_task.get("credits_locked", 0)
            else:
                content[image_index] = {
                    "type": "image",
                    "url": None,
                    "failed": True,
                    "error": the_task.get("error_message", "生成失败"),
                }

            # 3. 确定消息状态（至少 1 张有效图片 → completed，全部无效 → failed）
            has_valid = any(
                isinstance(c, dict) and c.get("url") and not c.get("failed")
                for c in content
            )
            msg_status = "completed" if has_valid else "failed"

            # 4. 更新消息（merge-update，不替换整条记录）
            self.db.table("messages").update({
                "content": content,
                "status": msg_status,
                "credits_cost": current_credits,
            }).eq("id", message_id).execute()

            # 5. 推送 message_done
            msg_data = {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": content,
                "status": msg_status,
                "credits_cost": current_credits,
                "generation_params": msg_result.data.get("generation_params"),
                "created_at": msg_result.data.get("created_at"),
            }

            done_msg = build_message_done(
                task_id=client_task_id or the_task["external_task_id"],
                conversation_id=conversation_id,
                message=msg_data,
                credits_consumed=the_task.get("credits_locked", 0),
            )

            await ws_manager.send_to_task_or_user(
                task_id=client_task_id or the_task["external_task_id"],
                user_id=user_id,
                message=done_msg,
            )

            logger.info(
                f"Single image finalized | batch_id={batch_id} | "
                f"message_id={message_id} | image_index={image_index} | "
                f"status={msg_status}"
            )
        except Exception as e:
            logger.error(
                f"Failed to finalize single image | batch_id={batch_id} | "
                f"message_id={message_id} | user_id={user_id} | "
                f"image_index={image_index} | error={e}"
            )

        # 释放任务限制槽位（无论 finalize 成功与否都要释放）
        await self._release_slot(the_task)

    async def _finalize_batch(
        self,
        batch_id: str,
        batch_tasks: List[Dict[str, Any]],
    ) -> None:
        """
        批次全部终态后的最终处理

        通过 upsert ON CONFLICT id 实现幂等（并发安全）。
        """
        if not batch_tasks:
            return

        # 取第一个 task 获取公共信息
        first_task = batch_tasks[0]
        message_id = first_task["placeholder_message_id"]
        conversation_id = first_task["conversation_id"]
        client_task_id = first_task.get("client_task_id")
        user_id = first_task["user_id"]
        model_id = first_task.get("model_id", "unknown")

        # 1. 按 image_index 构建完整 content 数组
        content_dicts = []
        total_credits = 0

        for task in batch_tasks:
            if task["status"] == "completed" and task.get("result_data"):
                content_dicts.append(task["result_data"])
                total_credits += task.get("credits_locked", 0)
            else:
                # 失败的图片：标记 failed
                content_dicts.append({
                    "type": "image",
                    "url": None,
                    "failed": True,
                    "error": task.get("error_message", "生成失败"),
                })

        # 2. 确定消息状态（至少 1 张成功 → completed，全部失败 → failed）
        success_count = sum(1 for t in batch_tasks if t["status"] == "completed")
        msg_status = "completed" if success_count > 0 else "failed"

        # 3. Upsert message（幂等：ON CONFLICT id）
        generation_type = first_task.get("type", "image")
        # 从 request_params 提取前端渲染所需参数（避免 upsert 覆盖占位符阶段存的值）
        request_params = first_task.get("request_params") or {}
        if isinstance(request_params, str):
            request_params = json.loads(request_params)
        gen_params = {
            "type": generation_type,
            "model": model_id,
            "num_images": len(batch_tasks),
        }
        # 保留占位符阶段写入的渲染参数（避免 upsert 覆盖后丢失）
        for key in ("aspect_ratio", "resolution", "output_format"):
            if request_params.get(key):
                gen_params[key] = request_params[key]

        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content_dicts,
            "status": msg_status,
            "credits_cost": total_credits,
            "task_id": client_task_id or first_task["external_task_id"],
            "generation_params": gen_params,
        }

        upsert_result = (
            self.db.table("messages")
            .upsert(message_data, on_conflict="id")
            .execute()
        )

        if not upsert_result or not upsert_result.data:
            logger.error(f"Failed to finalize batch message | batch_id={batch_id}")
            return

        msg_data = upsert_result.data[0]

        # 4. 推送 message_done
        done_msg = build_message_done(
            task_id=client_task_id or first_task["external_task_id"],
            conversation_id=conversation_id,
            message=msg_data,
            credits_consumed=total_credits,
        )

        await ws_manager.send_to_task_or_user(
            task_id=client_task_id or first_task["external_task_id"],
            user_id=user_id,
            message=done_msg,
        )

        # 5. 更新对话预览
        preview_text = f"[图片×{len(batch_tasks)}]" if len(batch_tasks) > 1 else "[图片]"
        try:
            self.db.table("conversations").update({
                "last_message_preview": preview_text,
            }).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(
                f"Failed to update conversation preview | "
                f"conversation_id={conversation_id} | error={e}"
            )

        logger.info(
            f"Batch finalized | batch_id={batch_id} | "
            f"success={success_count}/{len(batch_tasks)} | "
            f"credits={total_credits} | status={msg_status}"
        )

        # 释放任务限制槽位
        await self._release_slot(first_task)

    async def _release_slot(self, task: Dict[str, Any]) -> None:
        """释放任务限制槽位"""
        from services.task_limit_service import release_task_slot
        await release_task_slot(task)

    def _confirm_credits(self, transaction_id: str) -> None:
        """确认积分扣除（复用 CreditMixin 逻辑）"""
        try:
            self.db.table("credit_transactions").update({
                "status": "confirmed",
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", transaction_id).eq("status", "pending").execute()
        except Exception as e:
            logger.error(f"Failed to confirm credits | tx={transaction_id} | error={e}")

    def _refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（原子操作：CAS检查+退回余额+更新状态在单个SQL事务内完成）。

        失败时向上抛出异常，由调用方决策处理。
        """
        try:
            result = self.db.rpc(
                'atomic_refund_credits',
                {'p_transaction_id': transaction_id}
            ).execute()

            data = result.data
            if data and data.get('refunded'):
                logger.info(
                    f"Credits refunded | transaction_id={transaction_id} | "
                    f"user_id={data.get('user_id')} | amount={data.get('amount')}"
                )
            else:
                reason = data.get('reason', 'unknown') if data else 'no_response'
                logger.warning(f"Refund skipped | tx={transaction_id} | reason={reason}")
        except Exception as e:
            logger.critical(f"CREDIT_LOSS_RISK: refund failed | tx={transaction_id} | error={e}")
            raise
