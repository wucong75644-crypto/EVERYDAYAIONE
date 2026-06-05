"""_check_idempotency cancel guard 单元测试

线上 bug：用户取消后 chat_handler on_complete 仍跑，_check_idempotency 因 OrgScopedDB
org_id 过滤查不到 message → 走"data inconsistency"兜底 → 继续 _upsert →
覆盖 cancel API 写的 status='interrupted' + interrupt_marker → LLM 失忆。

根治：_check_idempotency 识别 cancel 触发的 task，无论 message 能否查到都返回
stub Message 让上层早返回，跳过 _upsert。

详见 docs/document/TECH_用户中断与恢复机制.md §四.2
"""

from unittest.mock import MagicMock

from schemas.message import MessageStatus

from services.handlers.mixins.message_mixin import MessageMixin


class _StubMixin(MessageMixin):
    """暴露 _check_idempotency 用的最小子类"""
    def __init__(self, db):
        self.db = db


def _task(status, error_message=None):
    return {
        "status": status,
        "error_message": error_message,
        "placeholder_message_id": "msg_X",
        "conversation_id": "conv_Y",
    }


class TestCancelGuard:
    """cancel 触发 task 永远跳过 _upsert，不查 DB"""

    def test_cancelled_status_returns_stub(self):
        """task.status='cancelled' → 返回 stub Message，不调 db"""
        db = MagicMock()
        mixin = _StubMixin(db)
        result = mixin._check_idempotency(_task("cancelled"), "task_id_1")

        assert result is not None
        assert result.id == "msg_X"
        assert result.conversation_id == "conv_Y"
        assert result.status == MessageStatus.FAILED
        # 关键：不应触发任何 db.table().select() 调用
        db.table.assert_not_called()

    def test_failed_with_cancel_message_returns_stub(self):
        """task.status='failed' + error='用户取消了任务' → 同样跳过 _upsert"""
        db = MagicMock()
        mixin = _StubMixin(db)
        result = mixin._check_idempotency(
            _task("failed", error_message="用户取消了任务"),
            "task_id_2",
        )

        assert result is not None
        assert result.id == "msg_X"
        db.table.assert_not_called()

    def test_failed_with_other_error_falls_through(self):
        """task.status='failed' 但 error 不是用户取消 → 走原 terminal 分支查 db"""
        db = MagicMock()
        # mock select 返回不存在 → critical 兜底 → return None
        chain = MagicMock()
        chain.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            MagicMock(data=None)
        )
        db.table.return_value = chain

        mixin = _StubMixin(db)
        result = mixin._check_idempotency(
            _task("failed", error_message="系统错误"),
            "task_id_3",
        )

        # 走 critical 兜底分支，return None
        assert result is None
        db.table.assert_called_with("messages")

    def test_completed_status_queries_db(self):
        """task.status='completed' → 走正常 terminal 分支查 db（不被 cancel guard 拦）"""
        db = MagicMock()
        chain = MagicMock()
        chain.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
            MagicMock(data=None)
        )
        db.table.return_value = chain

        mixin = _StubMixin(db)
        result = mixin._check_idempotency(_task("completed"), "task_id_4")

        # message missing → return None
        assert result is None
        db.table.assert_called_with("messages")

    def test_pending_status_returns_none(self):
        """非终态 → return None，不查 db"""
        db = MagicMock()
        mixin = _StubMixin(db)
        result = mixin._check_idempotency(_task("pending"), "task_id_5")

        assert result is None
        db.table.assert_not_called()

    def test_streaming_status_returns_none(self):
        """streaming 状态 → 不拦截"""
        db = MagicMock()
        mixin = _StubMixin(db)
        result = mixin._check_idempotency(_task("streaming"), "task_id_6")

        assert result is None
        db.table.assert_not_called()
