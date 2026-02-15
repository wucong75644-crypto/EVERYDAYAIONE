"""
CreditMixin - 积分管理

提供积分操作的基础方法：
- 余额查询和检查
- 积分锁定（预扣）
- 积分确认/退回
- 直接扣除
"""

from uuid import uuid4
from datetime import datetime, timezone

from loguru import logger

from core.exceptions import InsufficientCreditsError


class CreditMixin:
    """
    积分管理 Mixin

    提供积分相关的基础操作：
    - 查询余额
    - 检查余额是否充足
    - 锁定积分（预扣，用于 Image/Video）
    - 确认扣除（任务成功时）
    - 退回积分（任务失败时）
    - 直接扣除（Chat 完成后使用）
    """

    def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        result = self.db.table("users").select("credits").eq("id", user_id).single().execute()
        if not result.data:
            return 0
        return result.data.get("credits", 0)

    def _check_balance(self, user_id: str, required: int) -> int:
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
        balance = self._get_user_balance(user_id)
        if balance < required:
            logger.warning(
                f"Insufficient credits | user_id={user_id} | "
                f"required={required} | current={balance}"
            )
            raise InsufficientCreditsError(required=required, current=balance)
        return balance

    def _lock_credits(
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
        current_credits = self._get_user_balance(user_id)
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
            current_credits = self._get_user_balance(user_id)
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

    def _confirm_deduct(self, transaction_id: str) -> None:
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

    def _refund_credits(self, transaction_id: str) -> None:
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

    def _deduct_directly(
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
                current = self._get_user_balance(user_id)
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
