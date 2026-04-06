"""
task_utils 单元测试

覆盖：
- save_accumulated_to_message: task_type 参数传递、默认值、upsert 成功/失败
- refund_task_credits: 成功/跳过/异常
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import MagicMock
from services.task_utils import save_accumulated_to_message, refund_task_credits


def _mock_db():
    db = MagicMock()
    db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "msg-1"}])
    return db


# ── save_accumulated_to_message 测试 ──────────────────────────


class TestSaveAccumulatedToMessage:

    def test_default_task_type_is_chat(self):
        """不传 task_type 时，generation_params.type 应为 chat"""
        db = _mock_db()
        result = save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="hello", model_id="gpt-4",
        )
        assert result is True
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["generation_params"]["type"] == "chat"

    def test_task_type_image(self):
        """传 task_type='image' 时，generation_params.type 应为 image"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="hello", task_type="image",
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["generation_params"]["type"] == "image"

    def test_task_type_video(self):
        """传 task_type='video' 时，generation_params.type 应为 video"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="hello", task_type="video",
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["generation_params"]["type"] == "video"

    def test_model_id_in_generation_params(self):
        """model_id 应写入 generation_params"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="content", model_id="gemini-pro",
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["generation_params"]["model"] == "gemini-pro"

    def test_upsert_fields_correct(self):
        """验证 upsert 写入的字段完整性"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="部分内容", model_id="gpt-4",
            client_task_id="client-1", task_type="chat",
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["id"] == "msg-1"
        assert upsert_data["conversation_id"] == "conv-1"
        assert upsert_data["role"] == "assistant"
        assert upsert_data["content"] == [{"type": "text", "text": "部分内容"}]
        assert upsert_data["status"] == "completed"
        assert upsert_data["credits_cost"] == 0
        assert upsert_data["task_id"] == "client-1"

    def test_upsert_failure_returns_false(self):
        """upsert 异常时返回 False，不抛异常"""
        db = MagicMock()
        db.table.return_value.upsert.return_value.execute.side_effect = Exception("DB error")
        result = save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="content",
        )
        assert result is False


# ── refund_task_credits 测试 ──────────────────────────────────


class TestRefundTaskCredits:

    def test_refund_success(self):
        """退款成功返回 True"""
        db = MagicMock()
        db.rpc.return_value.execute.return_value = MagicMock(
            data={"refunded": True, "user_id": "u1", "amount": 10}
        )
        assert refund_task_credits(db, "tx-1") is True
        db.rpc.assert_called_once_with('atomic_refund_credits', {'p_transaction_id': 'tx-1'})

    def test_refund_skipped(self):
        """已退款/不需退时返回 True"""
        db = MagicMock()
        db.rpc.return_value.execute.return_value = MagicMock(
            data={"refunded": False, "reason": "already_refunded"}
        )
        assert refund_task_credits(db, "tx-1") is True

    def test_refund_exception(self):
        """RPC 异常时返回 False"""
        db = MagicMock()
        db.rpc.return_value.execute.side_effect = Exception("RPC error")
        assert refund_task_credits(db, "tx-1") is False
