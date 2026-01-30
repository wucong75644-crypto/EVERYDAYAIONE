"""
对话服务

处理对话的创建、查询、更新、删除等业务逻辑。
"""

from typing import Optional

from loguru import logger
from supabase import Client

from core.exceptions import NotFoundError, PermissionDeniedError


class ConversationService:
    """对话服务类"""

    def __init__(self, db: Client):
        self.db = db

    async def create_conversation(
        self,
        user_id: str,
        title: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> dict:
        """
        创建新对话

        Args:
            user_id: 用户 ID
            title: 对话标题（可选）
            model_id: 模型 ID（可选）

        Returns:
            对话信息
        """
        conversation_data = {
            "user_id": user_id,
            "title": title or "新对话",
            "message_count": 0,
            "credits_consumed": 0,
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

    async def get_conversation(self, conversation_id: str, user_id: str) -> dict:
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
        # 验证对话 ID 有效性（防止前端传递 "null"、"undefined" 等无效字符串）
        if not conversation_id or conversation_id in ("null", "undefined", "None"):
            raise NotFoundError("对话", conversation_id)

        result = (
            self.db.table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .execute()
        )

        if not result.data:
            raise NotFoundError("对话", conversation_id)

        conversation = result.data[0]

        if conversation["user_id"] != user_id:
            raise PermissionDeniedError("无权访问此对话")

        return self._format_conversation(conversation)

    async def get_conversation_list(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
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
        # 单次查询同时获取数据和总数（count="exact" 在同一请求中返回计数）
        result = (
            self.db.table("conversations")
            .select("id, title, last_message_preview, model_id, updated_at", count="exact")
            .eq("user_id", user_id)
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
            }
            for conv in result.data
        ]

        return {
            "conversations": conversations,
            "total": result.count or 0,
        }

    async def update_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: Optional[str] = None,
        model_id: Optional[str] = None,
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
        # 先验证权限
        await self.get_conversation(conversation_id, user_id)

        # 构建更新数据
        update_data = {}
        if title is not None:
            update_data["title"] = title
        if model_id is not None:
            update_data["model_id"] = model_id

        if not update_data:
            # 如果没有任何更新数据，直接返回当前对话
            return await self.get_conversation(conversation_id, user_id)

        result = (
            self.db.table("conversations")
            .update(update_data)
            .eq("id", conversation_id)
            .execute()
        )

        if not result.data:
            raise NotFoundError("对话", conversation_id)

        logger.info(
            f"Conversation updated | conversation_id={conversation_id} | updates={update_data}"
        )

        return self._format_conversation(result.data[0])

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
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
        # 先验证权限
        await self.get_conversation(conversation_id, user_id)

        # 删除对话（消息会通过外键级联删除）
        self.db.table("conversations").delete().eq("id", conversation_id).execute()

        logger.info(
            f"Conversation deleted | conversation_id={conversation_id} | user_id={user_id}"
        )

        return True

    async def increment_message_count(
        self, conversation_id: str, credits_cost: int = 0
    ) -> None:
        """
        增加对话消息计数和积分消耗

        Args:
            conversation_id: 对话 ID
            credits_cost: 本次消耗积分
        """
        # 获取当前值
        result = (
            self.db.table("conversations")
            .select("message_count, credits_consumed")
            .eq("id", conversation_id)
            .execute()
        )

        if result.data:
            current = result.data[0]
            self.db.table("conversations").update({
                "message_count": current["message_count"] + 1,
                "credits_consumed": current["credits_consumed"] + credits_cost,
            }).eq("id", conversation_id).execute()

    async def update_last_message_preview(
        self, conversation_id: str, content: str
    ) -> None:
        """
        更新对话的最后消息预览

        Args:
            conversation_id: 对话 ID
            content: 消息内容
        """
        preview = content[:50] + "..." if len(content) > 50 else content
        self.db.table("conversations").update({
            "last_message_preview": preview,
        }).eq("id", conversation_id).execute()

    async def _get_last_message(self, conversation_id: str) -> Optional[str]:
        """获取对话最后一条消息内容"""
        result = (
            self.db.table("messages")
            .select("content")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            content = result.data[0]["content"]
            # 截断过长的内容
            return content[:50] + "..." if len(content) > 50 else content

        return None

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
        }
