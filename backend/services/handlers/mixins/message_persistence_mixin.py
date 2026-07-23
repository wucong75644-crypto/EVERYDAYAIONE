"""助手消息持久化、任务读取与幂等检查。"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import Message, MessageRole, MessageStatus


class MessagePersistenceMixin:
    """提供 MessageMixin 使用的数据库持久化边界。"""

    def _upsert_assistant_message(
        self,
        message_id: str,
        conversation_id: str,
        content_dicts: List[Dict[str, Any]],
        status: MessageStatus,
        credits_cost: int,
        client_task_id: str,
        generation_type: str,
        model_id: str,
        is_error: bool = False,
        error_dict: Optional[Dict[str, str]] = None,
        extra_generation_params: Optional[Dict[str, Any]] = None,
        turn_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> tuple[Message, Dict[str, Any]]:
        """Upsert 助手消息，并返回领域对象与数据库原始数据。"""
        existing_gen_params: Dict[str, Any] = {}
        try:
            existing = (
                self.db.table("messages")
                .select("generation_params")
                .eq("id", message_id)
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                existing_value = existing.data.get("generation_params") or {}
                if isinstance(existing_value, dict):
                    existing_gen_params = existing_value
        except Exception:
            pass

        gen_params: Dict[str, Any] = {**existing_gen_params}
        gen_params["type"] = generation_type
        gen_params["model"] = model_id
        if extra_generation_params:
            gen_params.update(extra_generation_params)

        import json as json_module
        max_generation_params_size = 8192
        generation_params_size = len(
            json_module.dumps(gen_params, ensure_ascii=False).encode()
        )
        if generation_params_size > max_generation_params_size:
            digest = gen_params.get("tool_digest")
            if digest and isinstance(digest, dict):
                digest.pop("tools", None)
                generation_params_size = len(
                    json_module.dumps(gen_params, ensure_ascii=False).encode()
                )
            if generation_params_size > max_generation_params_size:
                gen_params = {"type": generation_type, "model": model_id}
                logger.warning(
                    "generation_params truncated | "
                    f"original={generation_params_size}B"
                )

        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": MessageRole.ASSISTANT.value,
            "content": content_dicts,
            "status": status.value,
            "credits_cost": credits_cost,
            "task_id": client_task_id,
            "generation_params": gen_params,
        }
        if turn_id:
            message_data["turn_id"] = turn_id
        if reply_to_message_id:
            message_data["reply_to_message_id"] = reply_to_message_id
        if is_error:
            message_data["is_error"] = True

        upsert_result = self.db.table("messages").upsert(
            message_data, on_conflict="id"
        ).execute()
        if not upsert_result or not upsert_result.data:
            logger.error(f"Failed to upsert message | message_id={message_id}")
            raise Exception("创建/更新消息失败")

        msg_data = upsert_result.data[0]
        from schemas.message import MessageError

        message = Message(
            id=msg_data["id"],
            conversation_id=msg_data["conversation_id"],
            role=MessageRole(msg_data["role"]),
            content=content_dicts,
            status=status,
            error=MessageError(**error_dict) if error_dict else None,
            created_at=datetime.fromisoformat(
                msg_data["created_at"].replace("Z", "+00:00")
            ),
        )
        return message, msg_data

    def _get_task_context(self, task_id: str) -> Dict[str, Any]:
        """获取任务基本信息，不存在则抛异常"""
        task = self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")
        return task

    def _check_idempotency(
        self, task: Dict[str, Any], task_id: str
    ) -> Optional[Message]:
        """任务已终态则返回已有消息；取消任务保留中断锚点。"""
        if task.get("status") not in ("completed", "failed", "cancelled"):
            return None
        message_id = task["placeholder_message_id"]
        is_user_cancelled = (
            task.get("status") == "cancelled"
            or (
                task.get("status") == "failed"
                and task.get("error_message") == "用户取消了任务"
            )
        )
        if is_user_cancelled:
            logger.info(
                "Cancel-triggered task, skipping on_complete persistence | "
                f"task_id={task_id} | message_id={message_id} | "
                f"status={task.get('status')}"
            )
            return Message(
                id=message_id,
                conversation_id=task["conversation_id"],
                role=MessageRole.ASSISTANT,
                content=[],
                status=MessageStatus.FAILED,
                error=None,
                created_at=datetime.now(timezone.utc),
            )

        logger.warning(
            "Task already in terminal state, skipping duplicate processing | "
            f"task_id={task_id} | status={task.get('status')}"
        )
        try:
            existing_msg = (
                self.db.table("messages").select("*")
                .eq("id", message_id).maybe_single().execute()
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch existing message | task_id={task_id} | error={e}"
            )
            raise Exception(f"无法读取已有消息: {e}")

        if existing_msg and existing_msg.data:
            from schemas.message import MessageError
            error_obj = None
            if existing_msg.data.get("is_error"):
                error_obj = MessageError(code="UNKNOWN", message="任务失败")
            return Message(
                id=existing_msg.data["id"],
                conversation_id=existing_msg.data["conversation_id"],
                role=MessageRole(existing_msg.data["role"]),
                content=existing_msg.data["content"],
                status=MessageStatus(
                    existing_msg.data.get("status", "completed")
                ),
                error=error_obj,
                created_at=datetime.fromisoformat(
                    existing_msg.data["created_at"].replace("Z", "+00:00")
                ),
            )

        logger.critical(
            "Data inconsistency: task terminal but message missing | "
            f"task_id={task_id} | message_id={message_id}"
        )
        return None
