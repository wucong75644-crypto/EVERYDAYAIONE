"""
消息服务

处理消息的创建、查询等业务逻辑。
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncIterator

from loguru import logger
from supabase import Client

from core.config import get_settings
from core.exceptions import NotFoundError, PermissionDeniedError
from services.conversation_service import ConversationService
from services.adapters.kie.client import KieAPIError
from services.message_utils import format_message, deduct_user_credits
from services.message_ai_helpers import (
    prepare_ai_stream_client,
    stream_ai_response,
)


class MessageService:
    """消息服务类"""

    def __init__(self, db: Client):
        self.db = db
        self.conversation_service = ConversationService(db)

    async def create_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        role: str = "user",
        credits_cost: int = 0,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        is_error: bool = False,
        created_at: Optional[datetime] = None,
        generation_params: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        创建消息

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID（用于权限验证）
            content: 消息内容
            role: 消息角色 (user/assistant/system)
            credits_cost: 消耗积分
            image_url: 图片 URL（可选）
            video_url: 视频 URL（可选）
            is_error: 是否为错误消息
            generation_params: 生成参数（图片/视频生成时保存，用于重新生成）

        Returns:
            消息信息

        Raises:
            NotFoundError: 对话不存在
            PermissionDeniedError: 无权访问
        """
        # 验证对话权限
        await self.conversation_service.get_conversation(conversation_id, user_id)

        message_data = {
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "credits_cost": credits_cost,
            "is_error": is_error,
        }
        if image_url:
            message_data["image_url"] = image_url
        if video_url:
            message_data["video_url"] = video_url
        if created_at:
            message_data["created_at"] = created_at.isoformat()
        if generation_params:
            # 如果是 Pydantic 模型，转换为字典
            if hasattr(generation_params, 'model_dump'):
                message_data["generation_params"] = generation_params.model_dump()
            elif hasattr(generation_params, 'dict'):
                message_data["generation_params"] = generation_params.dict()
            else:
                message_data["generation_params"] = generation_params

        result = self.db.table("messages").insert(message_data).execute()

        if not result.data:
            logger.error(
                f"Failed to create message | conversation_id={conversation_id}"
            )
            raise Exception("创建消息失败")

        message = result.data[0]

        # 更新对话的消息计数和最后消息预览
        await self.conversation_service.increment_message_count(
            conversation_id, credits_cost
        )
        await self.conversation_service.update_last_message_preview(
            conversation_id, content
        )

        logger.info(
            f"Message created | message_id={message['id']} | "
            f"conversation_id={conversation_id} | role={role} | "
            f"image_url={message.get('image_url')} | video_url={message.get('video_url')}"
        )

        return format_message(message)

    async def create_error_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
    ) -> dict:
        """
        创建错误消息（AI 调用失败时）

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            content: 错误消息内容

        Returns:
            错误消息信息
        """
        # 验证对话权限
        await self.conversation_service.get_conversation(conversation_id, user_id)

        message_data = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
            "credits_cost": 0,
            "is_error": True,  # 标记为错误消息
        }

        result = self.db.table("messages").insert(message_data).execute()

        if not result.data:
            logger.error(
                f"Failed to create error message | conversation_id={conversation_id}"
            )
            raise Exception("创建错误消息失败")

        message = result.data[0]

        # 更新对话的最后消息预览
        await self.conversation_service.update_last_message_preview(
            conversation_id, content
        )

        logger.info(
            f"Error message created | message_id={message['id']} | "
            f"conversation_id={conversation_id}"
        )

        return format_message(message)

    async def get_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        before_id: Optional[str] = None,
    ) -> dict:
        """
        获取对话消息列表

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID（用于权限验证）
            limit: 返回数量限制
            offset: 偏移量
            before_id: 获取此消息之前的消息（可选）

        Returns:
            消息列表和元数据

        Raises:
            NotFoundError: 对话不存在
            PermissionDeniedError: 无权访问
        """
        # 验证对话权限
        await self.conversation_service.get_conversation(conversation_id, user_id)

        # 查询消息（按创建时间正序）
        query = (
            self.db.table("messages")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
        )

        # 如果指定了 before_id，获取该消息之前的消息
        if before_id:
            # 先获取 before_id 消息的创建时间
            before_msg = self.db.table("messages").select("created_at").eq("id", before_id).single().execute()
            if before_msg.data:
                query = query.lt("created_at", before_msg.data["created_at"])

        if limit:
            query = query.limit(limit)
        if offset:
            query = query.offset(offset)

        result = query.execute()

        messages = [format_message(msg) for msg in result.data]

        logger.info(
            f"Messages retrieved | conversation_id={conversation_id} | "
            f"count={len(messages)}"
        )

        return {
            "messages": messages,
            "total": len(messages),
            "limit": limit,
            "offset": offset,
        }

    async def get_message(
        self,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> dict:
        """
        获取单条消息

        Args:
            conversation_id: 对话 ID
            message_id: 消息 ID
            user_id: 用户 ID（用于权限验证）

        Returns:
            消息信息

        Raises:
            NotFoundError: 消息不存在
            PermissionDeniedError: 无权访问
        """
        # 验证对话权限
        await self.conversation_service.get_conversation(conversation_id, user_id)

        result = (
            self.db.table("messages")
            .select("*")
            .eq("id", message_id)
            .eq("conversation_id", conversation_id)
            .single()
            .execute()
        )

        if not result.data:
            logger.warning(
                f"Message not found | message_id={message_id} | "
                f"conversation_id={conversation_id}"
            )
            raise NotFoundError("消息不存在")

        message = result.data

        # 再次验证权限
        conversation = await self.conversation_service.get_conversation(
            conversation_id, user_id
        )
        if conversation["user_id"] != user_id:
            raise PermissionDeniedError("无权访问此消息")

        return format_message(message)

    async def delete_message(
        self,
        message_id: str,
        user_id: str,
    ) -> dict:
        """
        删除消息

        Args:
            message_id: 消息 ID
            user_id: 用户 ID（用于权限验证）

        Returns:
            删除的消息信息（id 和 conversation_id）

        Raises:
            NotFoundError: 消息不存在
            PermissionDeniedError: 无权删除此消息
        """
        # 查询消息，获取 conversation_id
        try:
            result = (
                self.db.table("messages")
                .select("id, conversation_id")
                .eq("id", message_id)
                .execute()
            )

            if not result.data or len(result.data) == 0:
                logger.warning(f"Message not found | message_id={message_id}")
                raise NotFoundError("消息", message_id)

            message = result.data[0]
            conversation_id = message["conversation_id"]
        except NotFoundError:
            raise
        except Exception as e:
            logger.error(
                f"Error querying message | message_id={message_id} | error={str(e)}"
            )
            raise

        # 验证对话权限（确保用户拥有该对话）
        conversation = await self.conversation_service.get_conversation(
            conversation_id, user_id
        )
        if conversation["user_id"] != user_id:
            logger.warning(
                f"Permission denied for delete | message_id={message_id} | "
                f"user_id={user_id} | owner_id={conversation['user_id']}"
            )
            raise PermissionDeniedError("无权删除此消息")

        # 执行删除
        self.db.table("messages").delete().eq("id", message_id).execute()

        logger.info(
            f"Message deleted | message_id={message_id} | "
            f"conversation_id={conversation_id} | user_id={user_id}"
        )

        return {
            "id": message["id"],
            "conversation_id": conversation_id,
        }

    async def _get_conversation_history(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        获取对话历史（用于 AI 上下文）

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            limit: 历史消息数量限制

        Returns:
            格式化的历史消息列表
        """
        result = await self.get_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
        )

        history = []
        for msg in result["messages"]:
            if msg["role"] in ("user", "assistant"):
                msg_data = {
                    "role": msg["role"],
                    "content": msg["content"],
                }
                # 如果有图片或视频，添加到 attachments
                attachments = []
                if msg.get("image_url"):
                    attachments.append({
                        "type": "image",
                        "url": msg["image_url"]
                    })
                if msg.get("video_url"):
                    attachments.append({
                        "type": "video",
                        "url": msg["video_url"]
                    })
                if attachments:
                    msg_data["attachments"] = attachments
                history.append(msg_data)

        return history

    async def _update_conversation_title_if_first_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
    ) -> None:
        """如果是第一条消息，更新对话标题"""
        conversation = await self.conversation_service.get_conversation(
            conversation_id, user_id
        )
        if conversation["message_count"] == 1 and conversation["title"] == "新对话":
            new_title = content[:20] + ("..." if len(content) > 20 else "")
            await self.conversation_service.update_conversation(
                conversation_id, user_id, new_title
            )
