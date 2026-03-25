"""
消息服务

处理消息的创建、查询等业务逻辑。
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from loguru import logger


from core.exceptions import NotFoundError, PermissionDeniedError
from services.conversation_service import ConversationService
from services.message_utils import format_message


class MessageService:
    """消息服务类"""

    def __init__(self, db):
        self.db = db
        self.conversation_service = ConversationService(db)

    # ❌ 旧方法已删除：create_message() 和 create_error_message()
    # 请使用 /generate API 和 handler 系统创建消息

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
        try:
            # 验证对话权限
            await self.conversation_service.get_conversation(conversation_id, user_id)

            # 查询消息（按创建时间降序：从新到旧，确保首次加载显示最新消息）
            query = (
                self.db.table("messages")
                .select("*")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=True)
            )

            # 如果指定了 before_id，获取该消息之前的消息
            if before_id:
                # 先获取 before_id 消息的创建时间
                before_msg = self.db.table("messages").select("created_at").eq("id", before_id).single().execute()
                if before_msg.data:
                    query = query.lt("created_at", before_msg.data["created_at"])

            # ✅ 修复：limit=0 时应返回空列表，而不是查询所有消息
            if limit == 0:
                return {
                    "messages": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                }

            if limit > 0:
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
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error getting messages | conversation_id={conversation_id} | "
                f"user_id={user_id} | limit={limit} | offset={offset} | "
                f"before_id={before_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="MESSAGE_GET_ERROR",
                message="获取消息列表失败",
                status_code=500,
            )

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
        try:
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
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error getting message | conversation_id={conversation_id} | "
                f"message_id={message_id} | user_id={user_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="MESSAGE_GET_SINGLE_ERROR",
                message="获取消息失败",
                status_code=500,
            )

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
        try:
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

            # 兜底：清理 tasks 表中关联的进行中任务（防止刷新后占位符重现）
            try:
                for field in ("placeholder_message_id", "assistant_message_id"):
                    self.db.table("tasks").update({
                        "status": "failed",
                        "error_message": "关联消息已被删除",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }).eq(field, message_id).in_(
                        "status", ["pending", "running"]
                    ).execute()
            except Exception as task_err:
                logger.warning(
                    f"Failed to clean up tasks for deleted message | "
                    f"message_id={message_id} | error={str(task_err)}"
                )

            logger.info(
                f"Message deleted | message_id={message_id} | "
                f"conversation_id={conversation_id} | user_id={user_id}"
            )

            return {
                "id": message["id"],
                "conversation_id": conversation_id,
            }
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error deleting message | message_id={message_id} | "
                f"user_id={user_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="MESSAGE_DELETE_ERROR",
                message="删除消息失败",
                status_code=500,
            )

    async def _get_conversation_history(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 0,  # ✅ 修改：10 → 0，完全移除上下文记忆（避免污染）
    ) -> List[Dict[str, Any]]:
        """
        获取对话历史（用于 AI 上下文）

        ⚠️ 任务0.2扩展修复（2026-02-01）：
        - 默认 limit=0，完全移除上下文记忆
        - 原因：避免历史对话污染新话题（如税收政策被回答为产品设计）
        - 副作用：失去连续对话能力，用户无法引用之前内容
        - 如需恢复：将 limit 改为 3-5（保留最近几条消息）

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            limit: 历史消息数量限制（默认0=无上下文）

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
                history.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

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
