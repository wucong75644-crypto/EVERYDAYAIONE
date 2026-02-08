"""
Handler 基类

统一的消息处理器抽象接口。
所有类型（chat/image/video/audio）的 Handler 都继承此类。

积分处理：
- Chat: 完成后按实际 token 扣除
- Image/Video: 开始前预扣，完成后确认，失败后退回
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageRole,
    MessageStatus,
    TextPart,
)
from core.exceptions import InsufficientCreditsError


class BaseHandler(ABC):
    """
    统一的消息处理器基类

    职责：
    1. 创建助手消息占位符
    2. 启动生成任务（同步/异步）
    3. 处理完成/错误回调
    4. 推送 WebSocket 消息
    """

    def __init__(self, db: Client):
        self.db = db

    @property
    @abstractmethod
    def handler_type(self) -> GenerationType:
        """Handler 类型"""
        pass

    @abstractmethod
    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
    ) -> str:
        """
        启动处理任务

        Args:
            message_id: 助手消息 ID（占位符）
            conversation_id: 对话 ID
            user_id: 用户 ID
            content: 用户输入内容
            params: 类型特定参数

        Returns:
            task_id: 任务 ID
        """
        pass

    @abstractmethod
    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """
        完成回调

        Args:
            task_id: 任务 ID
            result: 生成结果（ContentPart 数组）
            credits_consumed: 消耗积分

        Returns:
            更新后的消息
        """
        pass

    @abstractmethod
    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """
        错误回调

        Args:
            task_id: 任务 ID
            error_code: 错误代码
            error_message: 错误信息

        Returns:
            更新后的消息
        """
        pass

    # ========================================
    # 辅助方法
    # ========================================

    def _build_callback_url(self, provider_value: str) -> Optional[str]:
        """
        构建回调 URL，未配置则返回 None

        URL 格式：{base_url}/api/webhook/{provider}
        不同 Provider 走不同的回调路由。

        Args:
            provider_value: Provider 枚举值（如 "kie"、"google"）

        Returns:
            完整回调 URL，或 None（退回纯轮询模式）
        """
        from core.config import get_settings

        base_url = get_settings().callback_base_url
        if not base_url:
            return None
        # 去掉末尾斜杠
        return f"{base_url.rstrip('/')}/api/webhook/{provider_value}"

    def _extract_text_content(self, content: List[ContentPart]) -> str:
        """从 ContentPart 数组提取文本"""
        for part in content:
            if isinstance(part, TextPart):
                return part.text
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
        return ""

    def _extract_image_url(self, content: List[ContentPart]) -> Optional[str]:
        """从 ContentPart 数组提取图片 URL"""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image":
                return part.get("url")
        return None

    async def _get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        result = (
            self.db.table("tasks")
            .select("*")
            .eq("external_task_id", task_id)
            .maybe_single()
            .execute()
        )
        return result.data if result.data else None

    async def _update_message(
        self,
        message_id: str,
        content: Optional[List[Dict[str, Any]]] = None,
        status: Optional[MessageStatus] = None,
        credits_cost: Optional[int] = None,
        error: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """更新消息"""
        update_data: Dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}

        if content is not None:
            update_data["content"] = content
        if status is not None:
            update_data["status"] = status.value
        if credits_cost is not None:
            update_data["credits_cost"] = credits_cost
        if error is not None:
            update_data["error"] = error

        result = (
            self.db.table("messages")
            .update(update_data)
            .eq("id", message_id)
            .execute()
        )

        if not result.data:
            logger.error(f"Failed to update message | message_id={message_id}")
            raise Exception("更新消息失败")

        return result.data[0]

    async def _complete_task(self, task_id: str) -> None:
        """标记任务完成"""
        self.db.table("tasks").update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("external_task_id", task_id).execute()

    async def _fail_task(self, task_id: str, error_message: str) -> None:
        """标记任务失败"""
        self.db.table("tasks").update({
            "status": "failed",
            "error_message": error_message,
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("external_task_id", task_id).execute()

    # ========================================
    # 积分相关方法
    # ========================================

    async def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        result = self.db.table("users").select("credits").eq("id", user_id).single().execute()
        if not result.data:
            return 0
        return result.data.get("credits", 0)

    async def _check_balance(self, user_id: str, required: int) -> int:
        """
        检查余额是否足够

        Args:
            user_id: 用户 ID
            required: 需要的积分数

        Returns:
            当前余额

        Raises:
            InsufficientCreditsError: 余额不足
        """
        balance = await self._get_user_balance(user_id)
        if balance < required:
            logger.warning(
                f"Insufficient credits | user_id={user_id} | "
                f"required={required} | current={balance}"
            )
            raise InsufficientCreditsError(required=required, current=balance)
        return balance

    async def _lock_credits(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = "",
    ) -> str:
        """
        预扣积分（锁定）

        Args:
            task_id: 任务 ID（幂等键）
            user_id: 用户 ID
            amount: 锁定数量
            reason: 锁定原因

        Returns:
            transaction_id

        Raises:
            InsufficientCreditsError: 余额不足
        """
        transaction_id = str(uuid4())

        # 1. 检查余额
        current_credits = await self._get_user_balance(user_id)
        if current_credits < amount:
            raise InsufficientCreditsError(required=amount, current=current_credits)

        # 2. 原子扣除（使用乐观锁）
        new_balance = current_credits - amount
        update_result = self.db.table("users").update({
            "credits": new_balance,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user_id).eq("credits", current_credits).execute()

        if not update_result.data:
            # 乐观锁冲突，重试一次
            logger.warning(f"Credit lock optimistic lock conflict | user_id={user_id}")
            current_credits = await self._get_user_balance(user_id)
            if current_credits < amount:
                raise InsufficientCreditsError(required=amount, current=current_credits)

            new_balance = current_credits - amount
            update_result = self.db.table("users").update({
                "credits": new_balance,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", user_id).eq("credits", current_credits).execute()

            if not update_result.data:
                raise InsufficientCreditsError(required=amount, current=current_credits)

        # 3. 记录事务
        self.db.table("credit_transactions").insert({
            "id": transaction_id,
            "task_id": task_id,
            "user_id": user_id,
            "amount": amount,
            "type": "lock",
            "status": "pending",
            "reason": reason
        }).execute()

        logger.info(
            f"Credits locked | transaction_id={transaction_id} | "
            f"task_id={task_id} | user_id={user_id} | amount={amount}"
        )

        return transaction_id

    async def _confirm_deduct(self, transaction_id: str) -> None:
        """
        确认扣除（任务成功时调用）

        Args:
            transaction_id: 事务 ID
        """
        self.db.table("credit_transactions").update({
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info(f"Credits confirmed | transaction_id={transaction_id}")

    async def _refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（任务失败时调用）

        Args:
            transaction_id: 事务 ID
        """
        # 1. 获取事务信息
        tx_result = self.db.table("credit_transactions").select("*").eq(
            "id", transaction_id
        ).maybe_single().execute()

        if not tx_result.data:
            logger.warning(f"Refund failed: transaction not found | id={transaction_id}")
            return

        tx = tx_result.data
        if tx["status"] != "pending":
            logger.warning(
                f"Refund failed: status not pending | "
                f"id={transaction_id} | status={tx['status']}"
            )
            return

        # 2. 退回积分（原子增加）
        self.db.rpc(
            'refund_credits',
            {
                'p_user_id': tx["user_id"],
                'p_amount': tx["amount"]
            }
        ).execute()

        # 3. 更新事务状态
        self.db.table("credit_transactions").update({
            "status": "refunded",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info(
            f"Credits refunded | transaction_id={transaction_id} | "
            f"user_id={tx['user_id']} | amount={tx['amount']}"
        )

    async def _deduct_directly(
        self,
        user_id: str,
        amount: int,
        reason: str,
        change_type: str,
    ) -> int:
        """
        直接扣除积分（Chat 完成后使用）

        Args:
            user_id: 用户 ID
            amount: 扣除数量
            reason: 扣除原因
            change_type: 变更类型

        Returns:
            新余额

        Raises:
            InsufficientCreditsError: 余额不足
        """
        try:
            result = self.db.rpc(
                'deduct_credits_atomic',
                {
                    'p_user_id': user_id,
                    'p_amount': amount,
                    'p_reason': reason,
                    'p_change_type': change_type
                }
            ).execute()

            if not result.data or result.data.get('success') is False:
                current = await self._get_user_balance(user_id)
                raise InsufficientCreditsError(required=amount, current=current)

            new_balance = result.data.get('new_balance', 0)
            logger.info(
                f"Credits deducted | user_id={user_id} | amount={amount} | "
                f"new_balance={new_balance} | reason={reason}"
            )
            return new_balance

        except Exception as e:
            if "InsufficientCreditsError" in str(type(e)):
                raise
            logger.error(f"Credit deduction failed | user_id={user_id} | error={e}")
            # 扣除失败时不阻塞任务完成，只记录日志
            return -1
