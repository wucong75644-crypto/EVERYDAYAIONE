"""图片批次最终消息落库与完成通知。"""

import json
from typing import Any, Awaitable, Callable, Dict, List

from loguru import logger

from schemas.websocket import build_message_done


ReleaseSlot = Callable[[Dict[str, Any]], Awaitable[None]]
WebSocketSender = Callable[..., Awaitable[None]]


class BatchMessageFinalizer:
    """完成整批图片或单张重新生成的最终消息收尾。"""

    def __init__(
        self,
        db: Any,
        ws_sender: WebSocketSender,
        release_slot: ReleaseSlot,
    ) -> None:
        self.db = db
        self._ws_sender = ws_sender
        self._release_slot = release_slot

    async def finalize_single_image(
        self,
        batch_id: str,
        batch_tasks: List[Dict[str, Any]],
    ) -> None:
        """将单张重新生成结果合并到现有消息的目标槽位。"""
        if not batch_tasks:
            return

        task = batch_tasks[0]
        message_id = task["placeholder_message_id"]
        image_index = task.get("image_index", 0)
        client_task_id = task.get("client_task_id")
        user_id = task["user_id"]
        conversation_id = task["conversation_id"]

        try:
            msg_result = (
                self.db.table("messages")
                .select("content, credits_cost, generation_params, created_at")
                .eq("id", message_id)
                .single()
                .execute()
            )
            if not msg_result.data:
                raise ValueError(
                    "Message not found for single image finalize | "
                    f"message_id={message_id}"
                )

            content = msg_result.data.get("content", [])
            if isinstance(content, str):
                content = json.loads(content)
            current_credits = msg_result.data.get("credits_cost", 0)

            while len(content) <= image_index:
                content.append({"type": "image", "url": None})

            if task["status"] == "completed" and task.get("result_data"):
                content[image_index] = task["result_data"]
                current_credits += task.get("credits_locked", 0)
            else:
                content[image_index] = task.get("result_data") or {
                    "type": "image",
                    "url": None,
                    "failed": True,
                    "error": task.get("error_message", "生成失败"),
                }

            has_valid = any(
                isinstance(part, dict)
                and part.get("url")
                and not part.get("failed")
                for part in content
            )
            msg_status = "completed" if has_valid else "failed"

            self.db.table("messages").update({
                "content": content,
                "status": msg_status,
                "credits_cost": current_credits,
            }).eq("id", message_id).execute()

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
            task_id = client_task_id or task["external_task_id"]
            done_msg = build_message_done(
                task_id=task_id,
                conversation_id=conversation_id,
                message=msg_data,
                credits_consumed=task.get("credits_locked", 0),
            )
            await self._ws_sender(
                task_id=task_id,
                user_id=user_id,
                message=done_msg,
                org_id=task.get("org_id"),
            )

            logger.info(
                f"Single image finalized | batch_id={batch_id} | "
                f"message_id={message_id} | image_index={image_index} | "
                f"status={msg_status}"
            )
        except Exception as error:
            logger.error(
                f"Failed to finalize single image | batch_id={batch_id} | "
                f"message_id={message_id} | user_id={user_id} | "
                f"image_index={image_index} | error={error}"
            )

        await self._release_slot(task)

    async def finalize_batch(
        self,
        batch_id: str,
        batch_tasks: List[Dict[str, Any]],
    ) -> None:
        """将终态图片任务汇总为完整助手消息。"""
        if not batch_tasks:
            return

        first_task = batch_tasks[0]
        message_id = first_task["placeholder_message_id"]
        conversation_id = first_task["conversation_id"]
        client_task_id = first_task.get("client_task_id")
        user_id = first_task["user_id"]
        model_id = first_task.get("model_id", "unknown")

        content = []
        total_credits = 0
        for task in batch_tasks:
            if task["status"] == "completed" and task.get("result_data"):
                content.append(task["result_data"])
                total_credits += task.get("credits_locked", 0)
            else:
                content.append(task.get("result_data") or {
                    "type": "image",
                    "url": None,
                    "failed": True,
                    "error": task.get("error_message", "生成失败"),
                })

        success_count = sum(
            1 for task in batch_tasks if task["status"] == "completed"
        )
        msg_status = "completed" if success_count > 0 else "failed"

        request_params = first_task.get("request_params") or {}
        if isinstance(request_params, str):
            request_params = json.loads(request_params)
        generation_params = {
            "type": first_task.get("type", "image"),
            "model": model_id,
            "num_images": len(batch_tasks),
        }
        for key in ("aspect_ratio", "resolution", "output_format"):
            if request_params.get(key):
                generation_params[key] = request_params[key]

        task_id = client_task_id or first_task["external_task_id"]
        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
            "status": msg_status,
            "credits_cost": total_credits,
            "task_id": task_id,
            "generation_params": generation_params,
        }
        upsert_result = (
            self.db.table("messages")
            .upsert(message_data, on_conflict="id")
            .execute()
        )
        if not upsert_result or not upsert_result.data:
            logger.error(f"Failed to finalize batch message | batch_id={batch_id}")
            return

        done_msg = build_message_done(
            task_id=task_id,
            conversation_id=conversation_id,
            message=upsert_result.data[0],
            credits_consumed=total_credits,
        )
        await self._ws_sender(
            task_id=task_id,
            user_id=user_id,
            message=done_msg,
            org_id=first_task.get("org_id"),
        )

        preview = f"[图片×{len(batch_tasks)}]" if len(batch_tasks) > 1 else "[图片]"
        try:
            self.db.table("conversations").update({
                "last_message_preview": preview,
            }).eq("id", conversation_id).execute()
        except Exception as error:
            logger.warning(
                "Failed to update conversation preview | "
                f"conversation_id={conversation_id} | error={error}"
            )

        logger.info(
            f"Batch finalized | batch_id={batch_id} | "
            f"success={success_count}/{len(batch_tasks)} | "
            f"credits={total_credits} | status={msg_status}"
        )
        await self._release_slot(first_task)
