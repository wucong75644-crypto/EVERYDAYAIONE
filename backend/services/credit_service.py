"""
积分服务

提供积分管理功能：
1. 原子扣除（deduct_atomic）：简单场景，直接扣除
2. 锁定模式（credit_lock）：复杂场景，先锁定再确认/退回
"""
from typing import Optional
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import datetime, timezone

from supabase import AsyncClient as SupabaseClient
from redis.asyncio import Redis
from loguru import logger

from core.exceptions import InsufficientCreditsError


class CreditService:
    """
    积分服务

    支持两种模式：
    1. 原子扣除（deduct_atomic）：简单场景，直接扣除
    2. 锁定模式（credit_lock）：复杂场景，先锁定再确认/退回
    """

    def __init__(self, db: SupabaseClient, redis: Optional[Redis] = None):
        self.db = db
        self.redis = redis

    async def get_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        result = await self.db.table("users").select("credits").eq("id", user_id).single().execute()
        if not result.data:
            return 0
        return result.data.get("credits", 0)

    async def deduct_atomic(
        self,
        user_id: str,
        amount: int,
        reason: str,
        change_type: str
    ) -> int:
        """
        原子扣除积分

        使用 RPC 函数保证原子性：
        UPDATE users SET credits = credits - amount
        WHERE id = user_id AND credits >= amount

        Args:
            user_id: 用户ID
            amount: 扣除数量
            reason: 扣除原因
            change_type: 变更类型（枚举值）

        Returns:
            新余额

        Raises:
            InsufficientCreditsError: 余额不足
        """
        result = await self.db.rpc(
            'deduct_credits_atomic',
            {
                'p_user_id': user_id,
                'p_amount': amount,
                'p_reason': reason,
                'p_change_type': change_type
            }
        ).execute()

        if not result.data or result.data.get('success') is False:
            logger.warning(
                "积分扣除失败：余额不足",
                user_id=user_id,
                amount=amount,
                reason=reason
            )
            raise InsufficientCreditsError("积分不足")

        new_balance = result.data.get('new_balance', 0)
        logger.info(
            "积分扣除成功",
            user_id=user_id,
            amount=amount,
            new_balance=new_balance,
            reason=reason
        )
        return new_balance

    async def lock_credits(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = ""
    ) -> str:
        """
        预扣积分（锁定）

        Args:
            task_id: 任务ID（幂等键）
            user_id: 用户ID
            amount: 锁定数量
            reason: 锁定原因

        Returns:
            transaction_id

        Raises:
            InsufficientCreditsError: 余额不足
        """
        transaction_id = str(uuid4())

        # 1. 检查余额
        current_credits = await self.get_balance(user_id)
        if current_credits < amount:
            logger.warning(
                "积分锁定失败：余额不足",
                user_id=user_id,
                amount=amount,
                current=current_credits
            )
            raise InsufficientCreditsError(
                f"积分不足，当前余额 {current_credits}，需要 {amount}"
            )

        # 2. 原子扣除（使用乐观锁）
        new_balance = current_credits - amount
        update_result = await self.db.table("users").update({
            "credits": new_balance,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user_id).eq("credits", current_credits).execute()

        if not update_result.data:
            # 乐观锁冲突，重试一次
            logger.warning("积分锁定乐观锁冲突，重试", user_id=user_id)
            return await self.lock_credits(task_id, user_id, amount, reason)

        # 3. 记录事务
        await self.db.table("credit_transactions").insert({
            "id": transaction_id,
            "task_id": task_id,
            "user_id": user_id,
            "amount": amount,
            "type": "lock",
            "status": "pending",
            "reason": reason
        }).execute()

        logger.info(
            "积分锁定成功",
            transaction_id=transaction_id,
            task_id=task_id,
            user_id=user_id,
            amount=amount
        )

        return transaction_id

    async def confirm_deduct(self, transaction_id: str) -> None:
        """
        确认扣除（任务成功时调用）

        Args:
            transaction_id: 事务ID
        """
        await self.db.table("credit_transactions").update({
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info("积分扣除确认", transaction_id=transaction_id)

    async def refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（任务失败时调用）

        Args:
            transaction_id: 事务ID
        """
        # 1. 获取事务信息
        tx_result = await self.db.table("credit_transactions").select("*").eq("id", transaction_id).single().execute()
        if not tx_result.data:
            logger.warning("退回失败：事务不存在", transaction_id=transaction_id)
            return

        tx = tx_result.data
        if tx["status"] != "pending":
            logger.warning(
                "退回失败：事务状态不是 pending",
                transaction_id=transaction_id,
                status=tx["status"]
            )
            return

        # 2. 退回积分
        await self.db.rpc(
            'refund_credits',
            {
                'p_user_id': tx["user_id"],
                'p_amount': tx["amount"]
            }
        ).execute()

        # 3. 更新事务状态
        await self.db.table("credit_transactions").update({
            "status": "refunded",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()

        logger.info(
            "积分退回成功",
            transaction_id=transaction_id,
            user_id=tx["user_id"],
            amount=tx["amount"]
        )

    @asynccontextmanager
    async def credit_lock(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = ""
    ):
        """
        积分锁定上下文管理器

        正常退出：自动确认扣除
        异常退出：自动退回积分

        Usage:
            async with credit_service.credit_lock(task_id, user_id, 10) as tx_id:
                result = await do_something()
                # 成功则自动确认
            # 异常则自动退回
        """
        transaction_id = await self.lock_credits(task_id, user_id, amount, reason)
        try:
            yield transaction_id
            # 正常退出，确认扣除
            await self.confirm_deduct(transaction_id)
        except Exception as e:
            # 异常退出，退回积分
            logger.error(
                "任务失败，退回积分",
                transaction_id=transaction_id,
                error=str(e)
            )
            await self.refund_credits(transaction_id)
            raise


# 依赖注入工厂函数
async def get_credit_service(
    db: SupabaseClient,
    redis: Optional[Redis] = None
) -> CreditService:
    """获取积分服务实例"""
    return CreditService(db, redis)
