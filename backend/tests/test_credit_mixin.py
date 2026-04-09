"""
CreditMixin 单元测试

测试积分 mixin 的错误处理改造：
- _refund_credits: RPC 失败时向上抛出异常
- _deduct_directly: RPC 失败时返回 0（不返回 -1）
"""

import pytest
from unittest.mock import MagicMock

from services.handlers.mixins.credit_mixin import CreditMixin
from core.exceptions import InsufficientCreditsError


class _MixinHost(CreditMixin):
    """宿主类，提供 db 属性"""
    def __init__(self, db):
        self.db = db


def _make_host() -> _MixinHost:
    return _MixinHost(db=MagicMock())


# ============================================================
# _refund_credits
# ============================================================


class TestRefundCredits:

    def test_success(self):
        """退回成功，不抛异常"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(
            data={"refunded": True, "user_id": "u1", "amount": 10}
        )
        host.db.rpc.return_value = mock_rpc

        host._refund_credits("tx_123")  # 不抛异常

    def test_already_refunded_no_exception(self):
        """已退回/非 pending 不抛异常"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(
            data={"refunded": False, "reason": "status_confirmed"}
        )
        host.db.rpc.return_value = mock_rpc

        host._refund_credits("tx_456")  # 不抛

    def test_rpc_failure_raises(self):
        """RPC 调用失败 → 向上抛出，不吞异常"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.side_effect = Exception("connection timeout")
        host.db.rpc.return_value = mock_rpc

        with pytest.raises(Exception, match="connection timeout"):
            host._refund_credits("tx_fail")


# ============================================================
# _deduct_directly
# ============================================================


class TestDeductDirectly:

    def test_success_returns_balance(self):
        """扣除成功返回新余额"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(
            data={"success": True, "new_balance": 90}
        )
        host.db.rpc.return_value = mock_rpc

        result = host._deduct_directly("u1", 10, "chat", "conversation_cost")
        assert result == 90

    def test_insufficient_credits_raises(self):
        """余额不足抛 InsufficientCreditsError"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data={"success": False})
        host.db.rpc.return_value = mock_rpc

        # _get_user_balance 需要 mock
        host.db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"credits": 5}
        )

        with pytest.raises(InsufficientCreditsError):
            host._deduct_directly("u1", 100, "chat", "conversation_cost")

    def test_rpc_failure_returns_zero_not_negative(self):
        """RPC 失败返回 0，不返回 -1"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.side_effect = Exception("DB down")
        host.db.rpc.return_value = mock_rpc

        result = host._deduct_directly("u1", 10, "chat", "conversation_cost")
        assert result == 0  # 不是 -1

    def test_rpc_failure_does_not_raise(self):
        """RPC 失败不阻塞调用方"""
        host = _make_host()
        mock_rpc = MagicMock()
        mock_rpc.execute.side_effect = RuntimeError("unexpected")
        host.db.rpc.return_value = mock_rpc

        # 不应抛异常
        result = host._deduct_directly("u1", 5, "chat", "conversation_cost")
        assert result == 0
