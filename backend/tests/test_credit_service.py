"""
credit_service 单元测试

测试积分服务的核心功能：
- 获取余额
- 原子扣除
- 积分锁定/确认/退回
- 上下文管理器
"""

import sys
from pathlib import Path

# Python path fix: 避免与根目录的 tests/ 冲突
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from services.credit_service import CreditService, CreditLockHandle
from core.exceptions import InsufficientCreditsError

# 测试辅助函数（避免导入冲突）
def create_test_user(
    user_id: str = None,
    phone: str = "13800138000",
    nickname: str = "测试用户",
    credits: int = 100,
    status: str = "active",
    role: str = "user",
    password_hash: str = None,
) -> dict:
    """创建测试用户数据"""
    from datetime import datetime, timezone
    return {
        "id": user_id or str(uuid4()),
        "phone": phone,
        "nickname": nickname,
        "credits": credits,
        "status": status,
        "role": role,
        "password_hash": password_hash,
        "avatar_url": None,
        "login_methods": ["phone"],
        "created_by": "phone",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }


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
        mock_async_db.set_table_data("credit_transactions", [])

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
        mock_async_db.table("credit_transactions").execute = MagicMock(
            return_value=MagicMock(data=[{}])
        )

        # Act - 应该不抛异常
        await credit_service.confirm_deduct("tx_123")

    @pytest.mark.asyncio
    async def test_refund_credits_success(self, credit_service, mock_async_db):
        """测试：退回积分成功（原子RPC）"""
        # Arrange - mock atomic_refund_credits RPC 返回成功
        mock_async_db.set_rpc_result("atomic_refund_credits", {
            "refunded": True,
            "user_id": "user_123",
            "amount": 10
        })

        # Act - 应该不抛异常
        await credit_service.refund_credits("tx_123")

    @pytest.mark.asyncio
    async def test_refund_credits_not_pending(self, credit_service, mock_async_db):
        """测试：非 pending 状态不退回（原子RPC返回 refunded=false）"""
        # Arrange - mock atomic_refund_credits RPC 返回跳过
        mock_async_db.set_rpc_result("atomic_refund_credits", {
            "refunded": False,
            "reason": "status_confirmed"
        })

        # Act - 应该静默返回，不退回
        await credit_service.refund_credits("tx_123")


class TestCreditServiceContextManager:
    """上下文管理器测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_credit_lock_success_full_amount(self, credit_service, mock_async_db):
        """测试：上下文管理器正常退出自动全额确认（未调用 set_actual）"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])
        mock_async_db.set_table_data("credit_transactions", [])

        confirmed = False

        async def mock_confirm(_):
            nonlocal confirmed
            confirmed = True

        credit_service.confirm_deduct = mock_confirm

        # Act
        async with credit_service.credit_lock("task_1", user["id"], 10) as handle:
            assert handle.transaction_id is not None
            assert handle.locked_amount == 10

        # Assert — 未调用 set_actual，全额确认，无退回
        assert confirmed is True
        assert handle.actual_amount == 10
        assert handle.refund_amount == 0

    @pytest.mark.asyncio
    async def test_credit_lock_partial_confirm(self, credit_service, mock_async_db):
        """测试：按量计费 — set_actual 后只扣实际量，退回差额"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])
        mock_async_db.set_table_data("credit_transactions", [])

        confirmed = False
        partial_refunded = False

        async def mock_confirm(_):
            nonlocal confirmed
            confirmed = True

        async def mock_partial_refund(tx_id, user_id, amount, org_id=None):
            nonlocal partial_refunded
            partial_refunded = True
            assert amount == 7  # 锁定10，实际3，退回7
            return True

        credit_service.confirm_deduct = mock_confirm
        credit_service._partial_refund = mock_partial_refund

        # Act
        async with credit_service.credit_lock("task_1", user["id"], 10) as handle:
            handle.set_actual(3)

        # Assert
        assert confirmed is True
        assert partial_refunded is True
        assert handle.actual_amount == 3
        assert handle.refund_amount == 7

    @pytest.mark.asyncio
    async def test_credit_lock_exception_refunds(self, credit_service, mock_async_db):
        """测试：上下文管理器异常退出自动退回"""
        # Arrange
        user = create_test_user(credits=100)
        mock_async_db.set_table_data("users", [user])
        mock_async_db.set_table_data("credit_transactions", [])

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


class TestCreditLockHandle:
    """CreditLockHandle 单元测试"""

    def test_default_actual_equals_locked(self):
        """未调用 set_actual 时，actual = locked"""
        handle = CreditLockHandle("tx_1", 10)
        assert handle.actual_amount == 10
        assert handle.refund_amount == 0

    def test_set_actual_normal(self):
        """正常设置实际量"""
        handle = CreditLockHandle("tx_1", 10)
        handle.set_actual(3)
        assert handle.actual_amount == 3
        assert handle.refund_amount == 7

    def test_set_actual_clamp_to_min_1(self):
        """实际量不能低于 1"""
        handle = CreditLockHandle("tx_1", 10)
        handle.set_actual(0)
        assert handle.actual_amount == 1
        assert handle.refund_amount == 9

    def test_set_actual_clamp_to_max(self):
        """实际量不能超过锁定量"""
        handle = CreditLockHandle("tx_1", 10)
        handle.set_actual(20)
        assert handle.actual_amount == 10
        assert handle.refund_amount == 0

    def test_set_actual_equal_to_locked(self):
        """实际量等于锁定量，无退回"""
        handle = CreditLockHandle("tx_1", 5)
        handle.set_actual(5)
        assert handle.actual_amount == 5
        assert handle.refund_amount == 0

    def test_final_credits_used_no_refund_needed(self):
        """无需退回时，final = actual = locked"""
        handle = CreditLockHandle("tx_1", 10)
        assert handle.final_credits_used == 10

    def test_final_credits_used_refund_succeeded(self):
        """退回成功时，final = actual"""
        handle = CreditLockHandle("tx_1", 10)
        handle.set_actual(3)
        handle._refund_succeeded = True
        assert handle.final_credits_used == 3

    def test_final_credits_used_refund_failed(self):
        """退回失败时，final = locked（用户被扣了全额）"""
        handle = CreditLockHandle("tx_1", 10)
        handle.set_actual(3)
        handle._refund_succeeded = False
        assert handle.final_credits_used == 10


class TestPartialRefund:
    """_partial_refund 独立测试"""

    @pytest.fixture
    def credit_service(self, mock_async_db):
        return CreditService(mock_async_db)

    @pytest.mark.asyncio
    async def test_partial_refund_success(self, credit_service, mock_async_db):
        """RPC 返回 refunded=True → 返回 True"""
        mock_async_db.set_rpc_result("partial_refund_credits", {
            "refunded": True, "new_balance": 97, "amount": 7,
        })

        result = await credit_service._partial_refund(
            "tx_1", "user_1", 7, org_id="org_1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_partial_refund_user_not_found(self, credit_service, mock_async_db):
        """RPC 返回 refunded=False → 返回 False"""
        mock_async_db.set_rpc_result("partial_refund_credits", {
            "refunded": False, "reason": "user_not_found",
        })

        result = await credit_service._partial_refund(
            "tx_1", "user_1", 7,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_refund_rpc_exception(self, credit_service):
        """RPC 抛异常 → 返回 False（不向上传播）"""
        mock_rpc = MagicMock()
        mock_rpc.execute.side_effect = Exception("connection timeout")
        credit_service.db.rpc = MagicMock(return_value=mock_rpc)

        result = await credit_service._partial_refund(
            "tx_1", "user_1", 5,
        )
        assert result is False


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
        mock_async_db.set_table_data("credit_transactions", [])

        # Act
        tx_id = await credit_service.lock_credits(
            task_id="task_123",
            user_id=user["id"],
            amount=50,  # 恰好等于余额
            reason="测试"
        )

        # Assert
        assert tx_id is not None
