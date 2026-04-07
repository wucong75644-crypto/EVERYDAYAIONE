"""
对话服务

处理对话的创建、查询、更新、删除等业务逻辑。
"""

from typing import Optional

from loguru import logger


from core.exceptions import NotFoundError, PermissionDeniedError


class ConversationService:
    """对话服务类"""

    def __init__(self, db):
        self.db = db

    async def create_conversation(
        self,
        user_id: str,
        title: Optional[str] = None,
        model_id: Optional[str] = None,
        org_id: Optional[str] = None,
        source: str = "web",
    ) -> dict:
        """
        创建新对话

        Args:
            user_id: 用户 ID
            title: 对话标题（可选）
            model_id: 模型 ID（可选）
            source: 来源（web / wecom）

        Returns:
            对话信息
        """
        try:
            conversation_data = {
                "user_id": user_id,
                "title": title or "新对话",
                "message_count": 0,
                "credits_consumed": 0,
                "org_id": org_id,
                "source": source,
            }

            if model_id:
                conversation_data["model_id"] = model_id

            result = self.db.table("conversations").insert(conversation_data).execute()

            if not result.data:
                logger.error(f"Failed to create conversation | user_id={user_id}")
                raise Exception("创建对话失败")

            conversation = result.data[0]
            logger.info(
                f"Conversation created | conversation_id={conversation['id']} | user_id={user_id}"
            )

            return self._format_conversation(conversation)
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error creating conversation | user_id={user_id} | "
                f"title={title} | model_id={model_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="CONVERSATION_CREATE_ERROR",
                message="创建对话失败",
                status_code=500,
            )

    async def get_conversation(
        self, conversation_id: str, user_id: str, org_id: Optional[str] = None,
    ) -> dict:
        """
        获取单个对话

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID（用于权限验证）

        Returns:
            对话信息

        Raises:
            NotFoundError: 对话不存在
            PermissionError: 无权访问
        """
        try:
            # 验证对话 ID 有效性（防止前端传递 "null"、"undefined" 等无效字符串）
            if not conversation_id or conversation_id in ("null", "undefined", "None"):
                raise NotFoundError("对话", conversation_id)

            query = (
                self.db.table("conversations")
                .select("*")
                .eq("id", conversation_id)
                .eq("user_id", user_id)
            )
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")
            result = query.execute()

            if not result.data:
                raise NotFoundError("对话", conversation_id)

            conversation = result.data[0]
            return self._format_conversation(conversation)
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error getting conversation | conversation_id={conversation_id} | "
                f"user_id={user_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="CONVERSATION_GET_ERROR",
                message="获取对话失败",
                status_code=500,
            )

    async def get_conversation_list(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        org_id: Optional[str] = None,
    ) -> dict:
        """
        获取用户对话列表

        Args:
            user_id: 用户 ID
            limit: 每页数量
            offset: 偏移量

        Returns:
            对话列表和总数
        """
        try:
            # 单次查询同时获取数据和总数（count="exact" 在同一请求中返回计数）
            query = (
                self.db.table("conversations")
                .select("id, title, last_message_preview, model_id, updated_at, source", count="exact")
                .eq("user_id", user_id)
            )
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")
            result = (
                query
                .order("updated_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )

            conversations = [
                {
                    "id": conv["id"],
                    "title": conv["title"],
                    "last_message": conv.get("last_message_preview"),
                    "model_id": conv.get("model_id"),
                    "updated_at": conv["updated_at"],
                    "source": conv.get("source", "web"),
                }
                for conv in result.data
            ]

            return {
                "conversations": conversations,
                "total": result.count or 0,
            }
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error getting conversation list | user_id={user_id} | "
                f"limit={limit} | offset={offset} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="CONVERSATION_LIST_ERROR",
                message="获取对话列表失败",
                status_code=500,
            )

    async def update_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: Optional[str] = None,
        model_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> dict:
        """
        更新对话信息

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            title: 新标题（可选）
            model_id: 模型 ID（可选）

        Returns:
            更新后的对话信息

        Raises:
            NotFoundError: 对话不存在
            PermissionError: 无权访问
        """
        try:
            # 先验证权限
            await self.get_conversation(conversation_id, user_id, org_id)

            # 构建更新数据
            update_data = {}
            if title is not None:
                update_data["title"] = title
            if model_id is not None:
                update_data["model_id"] = model_id

            if not update_data:
                return await self.get_conversation(conversation_id, user_id, org_id)

            query = (
                self.db.table("conversations")
                .update(update_data)
                .eq("id", conversation_id)
                .eq("user_id", user_id)
            )
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")
            result = query.execute()

            if not result.data:
                raise NotFoundError("对话", conversation_id)

            logger.info(
                f"Conversation updated | conversation_id={conversation_id} | updates={update_data}"
            )

            return self._format_conversation(result.data[0])
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error updating conversation | conversation_id={conversation_id} | "
                f"user_id={user_id} | title={title} | model_id={model_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="CONVERSATION_UPDATE_ERROR",
                message="更新对话失败",
                status_code=500,
            )

    async def delete_conversation(
        self, conversation_id: str, user_id: str, org_id: Optional[str] = None,
    ) -> bool:
        """
        删除对话

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID

        Returns:
            是否删除成功

        Raises:
            NotFoundError: 对话不存在
            PermissionError: 无权访问
        """
        try:
            # 先验证权限
            await self.get_conversation(conversation_id, user_id, org_id)

            # 删除对话（任务和消息会通过外键 CASCADE 级联删除）
            query = self.db.table("conversations").delete().eq("id", conversation_id).eq("user_id", user_id)
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")
            query.execute()

            logger.info(
                f"Conversation deleted | conversation_id={conversation_id} | user_id={user_id}"
            )

            return True
        except (NotFoundError, PermissionDeniedError):
            raise
        except Exception as e:
            logger.error(
                f"Error deleting conversation | conversation_id={conversation_id} | "
                f"user_id={user_id} | error={str(e)}"
            )
            from core.exceptions import AppException
            raise AppException(
                code="CONVERSATION_DELETE_ERROR",
                message="删除对话失败",
                status_code=500,
            )

    def _format_conversation(self, conversation: dict) -> dict:
        """格式化对话响应"""
        return {
            "id": conversation["id"],
            "user_id": conversation["user_id"],
            "title": conversation["title"],
            "model_id": conversation.get("model_id"),
            "message_count": conversation.get("message_count", 0),
            "credits_consumed": conversation.get("credits_consumed", 0),
            "created_at": conversation["created_at"],
            "updated_at": conversation["updated_at"],
            "context_summary": conversation.get("context_summary"),
        }
