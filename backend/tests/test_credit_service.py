"""
credit_service 单元测试

测试积分服务的核心功能：
- 获取余额
- 原子扣除
- 积分锁定/确认/退回
- 上下文管理器
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from services.credit_service import CreditService
from core.exceptions import InsufficientCreditsError
from tests.conftest import create_test_user


class TestCreditServiceBalance:
    """余额查询测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_get_balance_success(self, credit_service, mock_async_db):
        """测试：获取余额成功"""
        # Arrange
        user = create_test_user(credits=500)
        mock_async_db.set_table_data("users", [user])

        # Act
        balance = await credit_service.get_balance(user["id"])

        # Assert
        assert balance == 500

    @pytest.mark.asyncio
    async def test_get_balance_user_not_found(self, credit_service, mock_async_db):
        """测试：用户不存在返回 0"""
        # Arrange
        mock_async_db.set_table_data("users", [])

        # Act
        balance = await credit_service.get_balance("nonexistent")

        # Assert
        assert balance == 0


class TestCreditServiceDeductAtomic:
    """原子扣除测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_deduct_atomic_success(self, credit_service, mock_async_db):
        """测试：原子扣除成功"""
        # Arrange
        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": True,
            "new_balance": 90
        })

        # Act
        new_balance = await credit_service.deduct_atomic(
            user_id="user_123",
            amount=10,
            reason="测试扣除",
            change_type="usage"
        )

        # Assert
        assert new_balance == 90

    @pytest.mark.asyncio
    async def test_deduct_atomic_insufficient(self, credit_service, mock_async_db):
        """测试：余额不足"""
        # Arrange
        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": False
        })

        # Act & Assert
        with pytest.raises(InsufficientCreditsError):
            await credit_service.deduct_atomic(
                user_id="user_123",
                amount=1000,
                reason="测试扣除",
                change_type="usage"
            )


class TestCreditServiceLock:
    """积分锁定测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_lock_credits_success(self, credit_service, mock_async_db):
        """测试：锁定积分成功"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])

        # Mock update 成功
        mock_async_db.table("users").execute = AsyncMock(
            return_value=MagicMock(data=[{"credits": 90}])
        )
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # Act
        tx_id = await credit_service.lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=10,
            reason="测试锁定"
        )

        # Assert
        assert tx_id is not None
        assert len(tx_id) == 36  # UUID 格式

    @pytest.mark.asyncio
    async def test_lock_credits_insufficient(self, credit_service, mock_async_db):
        """测试：余额不足无法锁定"""
        # Arrange
        user = create_test_user(credits=5)
        mock_async_db.set_table_data("users", [user])

        # Act & Assert
        with pytest.raises(InsufficientCreditsError) as exc_info:
            await credit_service.lock_credits(
                task_id="task_123",
                user_id=user["id"],
                amount=100,
                reason="测试锁定"
            )

        assert "积分不足" in str(exc_info.value)


class TestCreditServiceConfirmAndRefund:
    """确认/退回测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_confirm_deduct(self, credit_service, mock_async_db):
        """测试：确认扣除"""
        # Arrange
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # Act - 应该不抛异常
        await credit_service.confirm_deduct("tx_123")

    @pytest.mark.asyncio
    async def test_refund_credits_success(self, credit_service, mock_async_db):
        """测试：退回积分成功"""
        # Arrange
        tx_data = {
            "id": "tx_123",
            "user_id": "user_123",
            "amount": 10,
            "status": "pending"
        }
        mock_async_db.set_table_data("credit_transactions", [tx_data])

        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=tx_data)
        )
        mock_async_db.rpc("refund_credits", {}).execute = AsyncMock(
            return_value=MagicMock(data={})
        )

        # Act - 应该不抛异常
        await credit_service.refund_credits("tx_123")

    @pytest.mark.asyncio
    async def test_refund_credits_not_pending(self, credit_service, mock_async_db):
        """测试：非 pending 状态不退回"""
        # Arrange
        tx_data = {
            "id": "tx_123",
            "user_id": "user_123",
            "amount": 10,
            "status": "confirmed"  # 已确认
        }
        mock_async_db.set_table_data("credit_transactions", [tx_data])

        # 模拟 single().execute() 返回
        mock_table = mock_async_db.table("credit_transactions")
        mock_table.select = MagicMock(return_value=mock_table)
        mock_table.eq = MagicMock(return_value=mock_table)
        mock_table.single = MagicMock(return_value=mock_table)
        mock_table.execute = AsyncMock(return_value=MagicMock(data=tx_data))

        # Act - 应该静默返回，不退回
        await credit_service.refund_credits("tx_123")


class TestCreditServiceContextManager:
    """上下文管理器测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_credit_lock_success(self, credit_service, mock_async_db):
        """测试：上下文管理器正常退出自动确认"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])

        mock_async_db.table("users").execute = AsyncMock(
            return_value=MagicMock(data=[{"credits": 90}])
        )
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        confirmed = False

        async def mock_confirm(_):
            nonlocal confirmed
            confirmed = True

        credit_service.confirm_deduct = mock_confirm

        # Act
        async with credit_service.credit_lock("task_1", user["id"], 10) as tx_id:
            assert tx_id is not None

        # Assert
        assert confirmed is True

    @pytest.mark.asyncio
    async def test_credit_lock_exception_refunds(self, credit_service, mock_async_db):
        """测试：上下文管理器异常退出自动退回"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])

        mock_async_db.table("users").execute = AsyncMock(
            return_value=MagicMock(data=[{"credits": 90}])
        )
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        refunded = False

        async def mock_refund(_):
            nonlocal refunded
            refunded = True

        credit_service.refund_credits = mock_refund

        # Act & Assert
        with pytest.raises(ValueError):
            async with credit_service.credit_lock("task_1", user["id"], 10):
                raise ValueError("模拟任务失败")

        assert refunded is True


class TestCreditServiceEdgeCases:
    """边界情况测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_deduct_zero_amount(self, credit_service, mock_async_db):
        """测试：扣除 0 积分"""
        # Arrange
        mock_async_db.set_rpc_result("deduct_credits_atomic", {
            "success": True,
            "new_balance": 100
        })

        # Act
        new_balance = await credit_service.deduct_atomic(
            user_id="user_123",
            amount=0,
            reason="零扣除",
            change_type="usage"
        )

        # Assert
        assert new_balance == 100

    @pytest.mark.asyncio
    async def test_lock_exact_balance(self, credit_service, mock_async_db):
        """测试：锁定恰好等于余额的积分"""
        # Arrange
        user = create_test_user(credits=50)
        mock_async_db.set_table_data("users", [user])

        mock_async_db.table("users").execute = AsyncMock(
            return_value=MagicMock(data=[{"credits": 0}])
        )
        mock_async_db.table("credit_transactions").execute = AsyncMock(
            return_value=MagicMock(data=[{}])
        )

        # Act
        tx_id = await credit_service.lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=50,  # 恰好等于余额
            reason="测试"
        )

        # Assert
        assert tx_id is not None
