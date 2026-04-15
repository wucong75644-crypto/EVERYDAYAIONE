"""
积分服务

提供积分管理功能：
1. 原子扣除（deduct_atomic）：简单场景，直接扣除
2. 锁定模式（credit_lock）：复杂场景，先锁定再确认/退回，支持按量计费
"""
from typing import Optional
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import datetime, timezone


from redis.asyncio import Redis
from loguru import logger

from core.exceptions import InsufficientCreditsError, AppException


class CreditLockHandle:
    """积分锁定句柄 — 由 credit_lock 上下文管理器 yield

    调用方可通过 set_actual(n) 设置实际消耗量，
    退出时只确认 actual_amount，差额自动退回。

    若未调用 set_actual，则按锁定全额确认（向后兼容）。
    """

    def __init__(self, transaction_id: str, locked_amount: int) -> None:
        self.transaction_id = transaction_id
        self.locked_amount = locked_amount
        self._actual_amount: Optional[int] = None
        self._refund_succeeded: Optional[bool] = None  # None=无需退回, True/False

    def set_actual(self, amount: int) -> None:
        """设置实际消耗积分（必须 >= 1 且 <= locked_amount）"""
        self._actual_amount = max(1, min(amount, self.locked_amount))

    @property
    def actual_amount(self) -> int:
        """实际扣费量：未设置则等于锁定量"""
        if self._actual_amount is not None:
            return self._actual_amount
        return self.locked_amount

    @property
    def refund_amount(self) -> int:
        """需退回的差额"""
        return self.locked_amount - self.actual_amount

    @property
    def final_credits_used(self) -> int:
        """最终实际扣费量（考虑退回是否成功）

        退回成功 → actual_amount
        退回失败 → locked_amount（用户实际被扣了全额）
        无需退回 → actual_amount == locked_amount
        """
        if self._refund_succeeded is False:
            return self.locked_amount
        return self.actual_amount


class CreditService:
    """
    积分服务

    支持两种模式：
    1. 原子扣除（deduct_atomic）：简单场景，直接扣除
    2. 锁定模式（credit_lock）：复杂场景，先锁定再确认/退回
    """

    def __init__(self, db, redis: Optional[Redis] = None):
        self.db = db
        self.redis = redis

    async def get_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        try:
            result = self.db.table("users").select("credits").eq("id", user_id).single().execute()
            if not result.data:
                return 0
            return result.data.get("credits", 0)
        except Exception as e:
            logger.error("获取积分余额失败", user_id=user_id, error=str(e))
            raise AppException(
                code="DATABASE_ERROR",
                message="获取积分余额失败",
                status_code=500
            )

    async def deduct_atomic(
        self,
        user_id: str,
        amount: int,
        reason: str,
        change_type: str,
        org_id: str | None = None,
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
        try:
            result = self.db.rpc(
                'deduct_credits_atomic',
                {
                    'p_user_id': user_id,
                    'p_amount': amount,
                    'p_reason': reason,
                    'p_change_type': change_type,
                    'p_org_id': org_id,
                }
            ).execute()

            if not result.data or result.data.get('success') is False:
                # 获取当前余额用于错误提示
                current_balance = await self.get_balance(user_id)
                logger.warning(
                    "积分扣除失败：余额不足",
                    user_id=user_id,
                    amount=amount,
                    current=current_balance,
                    reason=reason
                )
                raise InsufficientCreditsError(required=amount, current=current_balance)

            new_balance = result.data.get('new_balance', 0)
            logger.info(
                "积分扣除成功",
                user_id=user_id,
                amount=amount,
                new_balance=new_balance,
                reason=reason
            )
            return new_balance
        except InsufficientCreditsError:
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error("积分原子扣除失败", user_id=user_id, amount=amount, error=str(e))
            raise AppException(
                code="CREDIT_DEDUCT_FAILED",
                message="积分扣除失败",
                status_code=500
            )

    async def lock_credits(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = "",
        _retry_count: int = 0,
        org_id: str | None = None,
    ) -> str:
        """
        预扣积分（锁定）

        Args:
            task_id: 任务ID（幂等键）
            user_id: 用户ID
            amount: 锁定数量
            reason: 锁定原因
            _retry_count: 内部参数，重试计数（请勿外部传入）

        Returns:
            transaction_id

        Raises:
            InsufficientCreditsError: 余额不足或系统繁忙
        """
        try:
            MAX_RETRIES = 3
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
                raise InsufficientCreditsError(required=amount, current=current_credits)

            # 2. 原子扣除（使用乐观锁）
            new_balance = current_credits - amount
            update_result = self.db.table("users").update({
                "credits": new_balance,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", user_id).eq("credits", current_credits).execute()

            if not update_result.data:
                # 乐观锁冲突，有限重试
                if _retry_count >= MAX_RETRIES:
                    logger.error(
                        "积分锁定失败：乐观锁冲突超过最大重试次数",
                        user_id=user_id,
                        retry_count=_retry_count
                    )
                    raise AppException(
                        code="SYSTEM_BUSY",
                        message="系统繁忙，请稍后重试",
                        status_code=503
                    )

                logger.warning(
                    "积分锁定乐观锁冲突，重试",
                    user_id=user_id,
                    retry_count=_retry_count + 1
                )
                return await self.lock_credits(
                    task_id, user_id, amount, reason,
                    _retry_count=_retry_count + 1,
                    org_id=org_id,
                )

            # 3. 记录事务
            self.db.table("credit_transactions").insert({
                "id": transaction_id,
                "task_id": task_id,
                "user_id": user_id,
                "amount": amount,
                "type": "lock",
                "status": "pending",
                "reason": reason,
                "org_id": org_id,
            }).execute()

            logger.info(
                "积分锁定成功",
                transaction_id=transaction_id,
                task_id=task_id,
                user_id=user_id,
                amount=amount
            )

            return transaction_id
        except (InsufficientCreditsError, AppException):
            # 业务异常直接抛出
            raise
        except Exception as e:
            logger.error("积分锁定异常", task_id=task_id, user_id=user_id, amount=amount, error=str(e))
            raise AppException(
                code="CREDIT_LOCK_FAILED",
                message="积分锁定失败",
                status_code=500
            )

    async def confirm_deduct(self, transaction_id: str) -> None:
        """
        确认扣除（任务成功时调用）

        Args:
            transaction_id: 事务ID
        """
        try:
            self.db.table("credit_transactions").update({
                "status": "confirmed",
                "confirmed_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", transaction_id).execute()

            logger.info("积分扣除确认", transaction_id=transaction_id)
        except Exception as e:
            logger.error("确认积分扣除失败", transaction_id=transaction_id, error=str(e))
            raise AppException(
                code="CREDIT_CONFIRM_FAILED",
                message="确认积分扣除失败",
                status_code=500
            )

    async def refund_credits(self, transaction_id: str) -> None:
        """
        退回积分（原子操作：CAS检查+退回余额+更新状态在单个SQL事务内完成）

        Args:
            transaction_id: 事务ID
        """
        try:
            result = self.db.rpc(
                'atomic_refund_credits',
                {'p_transaction_id': transaction_id}
            ).execute()

            data = result.data
            if data and data.get('refunded'):
                logger.info(
                    "积分退回成功",
                    transaction_id=transaction_id,
                    user_id=data.get('user_id'),
                    amount=data.get('amount')
                )
            else:
                reason = data.get('reason', 'unknown') if data else 'no_response'
                logger.warning("退回跳过", transaction_id=transaction_id, reason=reason)
        except Exception as e:
            logger.error("退回积分失败", transaction_id=transaction_id, error=str(e))
            raise AppException(
                code="CREDIT_REFUND_FAILED",
                message="退回积分失败",
                status_code=500
            )

    async def _partial_refund(
        self,
        transaction_id: str,
        user_id: str,
        refund_amount: int,
        org_id: str | None = None,
    ) -> bool:
        """退回部分锁定积分（按量计费差额退回）

        与 refund_credits 不同：事务已 confirmed，这里只退回差额到用户余额。
        使用 partial_refund_credits RPC 保证原子性（余额+历史在同一事务）。
        迁移：079_partial_refund_credits.sql

        Returns:
            True=退回成功, False=失败（调用方据此决定 credits_used 记录值）
        """
        try:
            result = self.db.rpc(
                'partial_refund_credits',
                {
                    'p_user_id': user_id,
                    'p_refund_amount': refund_amount,
                    'p_description': f"按量计费差额退回 (tx={transaction_id})",
                    'p_org_id': org_id,
                }
            ).execute()
            data = result.data
            if data and data.get('refunded'):
                logger.info(
                    "部分退回成功",
                    transaction_id=transaction_id,
                    refund_amount=refund_amount,
                    new_balance=data.get('new_balance'),
                )
                return True
            reason = data.get('reason', 'unknown') if data else 'no_response'
            logger.warning(
                "部分退回跳过",
                transaction_id=transaction_id,
                reason=reason,
            )
            return False
        except Exception as e:
            logger.error(
                "部分退回失败",
                transaction_id=transaction_id,
                refund_amount=refund_amount,
                error=str(e),
            )
            return False

    @asynccontextmanager
    async def credit_lock(
        self,
        task_id: str,
        user_id: str,
        amount: int,
        reason: str = "",
        org_id: str | None = None,
    ):
        """
        积分锁定上下文管理器（支持按量计费）

        正常退出：
          - 若调用了 handle.set_actual(n)，只确认 n 积分，退回差额
          - 若未调用，全额确认（向后兼容）
        异常退出：自动退回全部积分

        Usage:
            async with credit_service.credit_lock(task_id, user_id, 10) as handle:
                result = await do_something()
                handle.set_actual(3)  # 实际只消耗 3 积分
            # 退出时自动确认 3，退回 7
        """
        transaction_id = await self.lock_credits(task_id, user_id, amount, reason, org_id=org_id)
        handle = CreditLockHandle(transaction_id, amount)
        try:
            yield handle
            # 正常退出，按实际量确认
            await self.confirm_deduct(transaction_id)
            if handle.refund_amount > 0:
                handle._refund_succeeded = await self._partial_refund(
                    transaction_id, user_id, handle.refund_amount,
                    org_id=org_id,
                )
                logger.info(
                    "按量计费退回差额",
                    transaction_id=transaction_id,
                    locked=amount,
                    actual=handle.actual_amount,
                    refunded=handle.refund_amount,
                    refund_ok=handle._refund_succeeded,
                )
        except Exception as e:
            # 异常退出，退回全部积分
            logger.error(
                "任务失败，退回积分",
                transaction_id=transaction_id,
                error=str(e)
            )
            await self.refund_credits(transaction_id)
            raise
