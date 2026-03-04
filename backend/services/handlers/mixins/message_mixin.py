"""
MessageMixin - 消息处理

提供消息 upsert 和完成/错误处理的通用流程：
- 消息 upsert 到数据库
- WebSocket 推送
- 完成/错误处理通用流程
"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from loguru import logger

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
)


class MessageMixin:
    """
    消息处理 Mixin

    提供消息相关的通用处理流程：
    - 助手消息 upsert（统一格式）
    - 完成处理（积分 + 消息 + WebSocket + 任务状态）
    - 错误处理（退回积分 + 错误消息 + WebSocket + 任务状态）
    """

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
    ) -> tuple[Message, Dict[str, Any]]:
        """
        通用的助手消息 upsert 方法

        Args:
            message_id: 消息 ID
            conversation_id: 对话 ID
            content_dicts: 内容字典列表
            status: 消息状态
            credits_cost: 积分消耗
            client_task_id: 客户端任务 ID
            generation_type: 生成类型（chat/image/video）
            model_id: 模型 ID
            is_error: 是否为错误消息
            error_dict: 错误详情（is_error=True 时提供）
            extra_generation_params: 额外的生成参数（如 aspect_ratio），合并到 generation_params

        Returns:
            (Message 对象, 原始字典数据)
        """
        # 1. 构建消息数据
        gen_params: Dict[str, Any] = {"type": generation_type, "model": model_id}
        if extra_generation_params:
            gen_params.update(extra_generation_params)

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

        if is_error:
            message_data["is_error"] = True
            message_data["error"] = error_dict

        # 2. Upsert 到数据库
        upsert_result = self.db.table("messages").upsert(
            message_data, on_conflict="id"
        ).execute()

        if not upsert_result or not upsert_result.data:
            logger.error(f"Failed to upsert message | message_id={message_id}")
            raise Exception("创建/更新消息失败")

        msg_data = upsert_result.data[0]

        # 3. 构建 Message 对象（注意：Message 类没有 is_error 字段）
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

    async def _handle_complete_common(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int,
    ) -> Message:
        """
        通用的完成处理流程

        Args:
            task_id: 任务 ID
            result: 生成结果
            credits_consumed: 消耗积分

        Returns:
            完成后的消息
        """
        # 1. 获取任务信息
        task = self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 2. 幂等性检查：避免重复扣除积分
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.warning(
                f"Task already in terminal state, skipping duplicate processing | "
                f"task_id={task_id} | status={task['status']}"
            )
            # 获取已存在的消息并返回（使用 maybe_single 防止异常）
            try:
                existing_msg = self.db.table("messages").select("*").eq("id", message_id).maybe_single().execute()
            except Exception as e:
                logger.error(f"Failed to fetch existing message | task_id={task_id} | error={e}")
                raise Exception(f"无法读取已完成的消息: {e}")

            if existing_msg and existing_msg.data:
                return Message(
                    id=existing_msg.data["id"],
                    conversation_id=existing_msg.data["conversation_id"],
                    role=MessageRole(existing_msg.data["role"]),
                    content=existing_msg.data["content"],
                    status=MessageStatus(existing_msg.data.get("status", "completed")),
                    error=None,  # 数据库不存储 error 详情，只有 is_error 标志
                    created_at=datetime.fromisoformat(
                        existing_msg.data["created_at"].replace("Z", "+00:00")
                    ),
                )
            else:
                # 任务已完成但消息不存在：数据不一致，记录严重错误但允许继续
                logger.critical(
                    f"Data inconsistency: task completed but message missing | "
                    f"task_id={task_id} | message_id={message_id} | "
                    f"Allowing continuation to recreate message"
                )
                # 不抛异常，让后续流程重新创建消息以恢复数据一致性

        # 3. 处理积分（子类实现）
        actual_credits = await self._handle_credits_on_complete(task, credits_consumed)

        # 4. 转换 ContentPart 为字典（子类实现）
        content_dicts = self._convert_content_parts_to_dicts(result)

        # 5. 从 request_params 提取前端渲染所需参数（避免 upsert 覆盖占位符阶段存的值）
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            import json
            request_params = json.loads(request_params)
        extra_gen_params = {}
        if request_params.get("aspect_ratio"):
            extra_gen_params["aspect_ratio"] = request_params["aspect_ratio"]

        # 6. Upsert 消息到数据库
        message, msg_data = self._upsert_assistant_message(
            message_id=message_id,
            conversation_id=conversation_id,
            content_dicts=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=actual_credits,
            client_task_id=client_task_id,
            generation_type=self.handler_type.value,
            model_id=model_id,
            extra_generation_params=extra_gen_params,
        )

        # 6. 推送 WebSocket 完成消息
        from schemas.websocket import build_message_done
        from services.websocket_manager import ws_manager

        done_msg = build_message_done(
            task_id=client_task_id,
            conversation_id=conversation_id,
            message=msg_data,
            credits_consumed=actual_credits,
        )

        # Chat 使用 send_to_task_subscribers，Media 使用 send_to_task_or_user
        if self.handler_type == GenerationType.CHAT:
            await ws_manager.send_to_task_subscribers(client_task_id, done_msg)
        else:
            user_id = task["user_id"]
            await ws_manager.send_to_task_or_user(client_task_id, user_id, done_msg)

        # 7. 更新任务状态
        self._complete_task(task_id)

        # 8. 更新对话预览（触发器自动更新 updated_at，保证排序正确）
        preview_text = content_dicts[0].get("text", "")[:50] if content_dicts else ""
        try:
            self.db.table("conversations").update({
                "last_message_preview": preview_text,
            }).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update conversation preview | conversation_id={conversation_id} | error={e}")

        # 9. 记录日志
        logger.info(
            f"{self.handler_type.value.capitalize()} completed | "
            f"task_id={task_id} | message_id={message_id} | credits={actual_credits}"
        )

        return message

    async def _handle_error_common(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """
        通用的错误处理流程

        Args:
            task_id: 任务 ID
            error_code: 错误代码
            error_message: 错误消息

        Returns:
            错误消息
        """
        # 1. 获取任务信息
        task = self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task_id

        # 2. 幂等性检查：避免重复处理
        if task.get('status') in ['completed', 'failed', 'cancelled']:
            logger.warning(
                f"Task already in terminal state, skipping duplicate error handling | "
                f"task_id={task_id} | status={task['status']}"
            )
            # 获取已存在的消息并返回（使用 maybe_single 防止异常）
            try:
                existing_msg = self.db.table("messages").select("*").eq("id", message_id).maybe_single().execute()
            except Exception as e:
                logger.error(f"Failed to fetch existing message | task_id={task_id} | error={e}")
                raise Exception(f"无法读取已失败的消息: {e}")

            if existing_msg and existing_msg.data:
                from schemas.message import MessageError

                # 根据 is_error 标志构造 error 对象（因为数据库不存储详情）
                error_obj = None
                if existing_msg.data.get("is_error"):
                    error_obj = MessageError(code="UNKNOWN", message="任务失败")

                return Message(
                    id=existing_msg.data["id"],
                    conversation_id=existing_msg.data["conversation_id"],
                    role=MessageRole(existing_msg.data["role"]),
                    content=existing_msg.data["content"],
                    status=MessageStatus(existing_msg.data.get("status", "failed")),
                    error=error_obj,
                    created_at=datetime.fromisoformat(
                        existing_msg.data["created_at"].replace("Z", "+00:00")
                    ),
                )
            else:
                # 任务已失败但消息不存在：数据不一致，记录严重错误但允许继续
                logger.critical(
                    f"Data inconsistency: task failed but message missing | "
                    f"task_id={task_id} | message_id={message_id} | "
                    f"Allowing continuation to recreate error message"
                )
                # 不抛异常，让后续流程重新创建错误消息

        # 3. 处理积分退回（子类实现）
        await self._handle_credits_on_error(task)

        # 4. 从 request_params 提取前端渲染所需参数
        request_params = task.get("request_params") or {}
        if isinstance(request_params, str):
            import json
            request_params = json.loads(request_params)
        extra_gen_params = {}
        if request_params.get("aspect_ratio"):
            extra_gen_params["aspect_ratio"] = request_params["aspect_ratio"]

        # 5. Upsert 错误消息到数据库
        message, msg_data = self._upsert_assistant_message(
            message_id=message_id,
            conversation_id=conversation_id,
            content_dicts=[{"type": "text", "text": error_message}],
            status=MessageStatus.FAILED,
            credits_cost=0,
            client_task_id=client_task_id,
            generation_type=self.handler_type.value,
            model_id=model_id,
            is_error=True,
            error_dict={"code": error_code, "message": error_message},
            extra_generation_params=extra_gen_params,
        )

        # 5. 推送 WebSocket 错误消息
        from schemas.websocket import build_message_error
        from services.websocket_manager import ws_manager

        error_msg = build_message_error(
            task_id=client_task_id,
            conversation_id=conversation_id,
            message_id=message_id,
            error_code=error_code,
            error_message=error_message,
        )

        # Chat 使用 send_to_task_subscribers，Media 使用 send_to_task_or_user
        if self.handler_type == GenerationType.CHAT:
            await ws_manager.send_to_task_subscribers(client_task_id, error_msg)
        else:
            user_id = task["user_id"]
            await ws_manager.send_to_task_or_user(client_task_id, user_id, error_msg)

        # 6. 更新任务状态
        self._fail_task(task_id, error_message)

        # 7. 记录日志
        logger.error(
            f"{self.handler_type.value.capitalize()} failed | "
            f"task_id={task_id} | error_code={error_code} | error={error_message}"
        )

        return message
