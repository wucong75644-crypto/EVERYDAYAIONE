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

        Returns:
            (Message 对象, 原始字典数据)
        """
        # 1. 构建消息数据
        message_data = {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": MessageRole.ASSISTANT.value,
            "content": content_dicts,
            "status": status.value,
            "credits_cost": credits_cost,
            "task_id": client_task_id,
            "generation_params": {"type": generation_type, "model": model_id},
        }

        if is_error:
            message_data["is_error"] = True
            message_data["error"] = error_dict

        # 2. Upsert 到数据库
        upsert_result = self.db.table("messages").upsert(
            message_data, on_conflict="id"
        ).execute()

        if not upsert_result.data:
            logger.error(f"Failed to upsert message | message_id={message_id}")
            raise Exception("创建/更新消息失败")

        msg_data = upsert_result.data[0]

        # 3. 构建 Message 对象
        message = Message(
            id=msg_data["id"],
            conversation_id=msg_data["conversation_id"],
            role=MessageRole(msg_data["role"]),
            content=content_dicts,
            status=status,
            is_error=is_error,
            error=error_dict,
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

        # 2. 处理积分（子类实现）
        actual_credits = await self._handle_credits_on_complete(task, credits_consumed)

        # 3. 转换 ContentPart 为字典（子类实现）
        content_dicts = self._convert_content_parts_to_dicts(result)

        # 4. Upsert 消息到数据库
        message, msg_data = self._upsert_assistant_message(
            message_id=message_id,
            conversation_id=conversation_id,
            content_dicts=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=actual_credits,
            client_task_id=client_task_id,
            generation_type=self.handler_type.value,
            model_id=model_id,
        )

        # 5. 推送 WebSocket 完成消息
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

        # 6. 更新任务状态
        self._complete_task(task_id)

        # 7. 记录日志
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

        # 2. 处理积分退回（子类实现）
        await self._handle_credits_on_error(task)

        # 3. Upsert 错误消息到数据库
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
        )

        # 4. 推送 WebSocket 错误消息
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

        # 5. 更新任务状态
        self._fail_task(task_id, error_message)

        # 6. 记录日志
        logger.error(
            f"{self.handler_type.value.capitalize()} failed | "
            f"task_id={task_id} | error_code={error_code} | error={error_message}"
        )

        return message
