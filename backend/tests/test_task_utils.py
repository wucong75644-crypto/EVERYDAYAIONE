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
from services.task_utils import save_accumulated_to_message, refund_task_credits, merge_blocks_with_text


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


# ── merge_blocks_with_text 测试 ───────────────────────────────


class TestMergeBlocksWithText:

    def test_blocks_with_matching_text(self):
        """blocks 中的 text 与 accumulated_text 前缀匹配，剩余文字追加"""
        blocks = [
            {"type": "text", "text": "turn1"},
            {"type": "tool_step", "tool_name": "data_query", "status": "completed"},
        ]
        result = merge_blocks_with_text(blocks, "turn1turn2 final")
        assert len(result) == 3
        assert result[0] == {"type": "text", "text": "turn1"}
        assert result[1]["type"] == "tool_step"
        assert result[2] == {"type": "text", "text": "turn2 final"}

    def test_blocks_no_remaining_text(self):
        """accumulated_text 与 blocks 文字完全一致，不追加额外 text"""
        blocks = [
            {"type": "text", "text": "all text"},
            {"type": "tool_step", "tool_name": "code_execute", "status": "completed"},
        ]
        result = merge_blocks_with_text(blocks, "all text")
        assert len(result) == 2

    def test_empty_blocks(self):
        """空 blocks 数组 + 有文字 → 追加剩余文字"""
        result = merge_blocks_with_text([], "some text")
        assert result == [{"type": "text", "text": "some text"}]

    def test_text_mismatch_fallback(self):
        """accumulated_text 与 blocks 文字不对齐时，不追加（安全降级）"""
        blocks = [
            {"type": "text", "text": "original"},
            {"type": "tool_step", "tool_name": "data_query", "status": "completed"},
        ]
        result = merge_blocks_with_text(blocks, "completely different")
        # 不对齐时 remaining 为空，不追加
        assert len(result) == 2

    def test_empty_accumulated_text(self):
        """accumulated_text 为空字符串，不追加"""
        blocks = [
            {"type": "tool_step", "tool_name": "data_query", "status": "running"},
        ]
        result = merge_blocks_with_text(blocks, "")
        assert len(result) == 1

    def test_remaining_only_whitespace(self):
        """剩余文字只有空白时不追加"""
        blocks = [{"type": "text", "text": "hello"}]
        result = merge_blocks_with_text(blocks, "hello   ")
        assert len(result) == 1

    def test_multiple_text_blocks(self):
        """多个 text block 正确拼接计算剩余"""
        blocks = [
            {"type": "text", "text": "turn1"},
            {"type": "tool_step", "tool_name": "t1", "status": "completed"},
            {"type": "text", "text": "turn2"},
            {"type": "tool_step", "tool_name": "t2", "status": "completed"},
        ]
        result = merge_blocks_with_text(blocks, "turn1turn2turn3 answer")
        assert len(result) == 5
        assert result[4] == {"type": "text", "text": "turn3 answer"}


class TestSaveAccumulatedWithBlocks:

    def test_with_blocks_merges_content(self):
        """传 accumulated_blocks 时，content 应包含 blocks + 剩余文字"""
        db = _mock_db()
        blocks = [
            {"type": "text", "text": "分析中"},
            {"type": "tool_step", "tool_name": "data_query", "status": "completed"},
        ]
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="分析中最终回答",
            accumulated_blocks=blocks,
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert len(upsert_data["content"]) == 3
        assert upsert_data["content"][0] == {"type": "text", "text": "分析中"}
        assert upsert_data["content"][1]["type"] == "tool_step"
        assert upsert_data["content"][2] == {"type": "text", "text": "最终回答"}

    def test_with_empty_blocks_fallback(self):
        """accumulated_blocks 为空列表时，走纯文字路径"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="plain text",
            accumulated_blocks=[],
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["content"] == [{"type": "text", "text": "plain text"}]

    def test_with_none_blocks_fallback(self):
        """accumulated_blocks 为 None 时，走纯文字路径"""
        db = _mock_db()
        save_accumulated_to_message(
            db, message_id="msg-1", conversation_id="conv-1",
            accumulated_content="plain text",
            accumulated_blocks=None,
        )
        upsert_data = db.table.return_value.upsert.call_args[0][0]
        assert upsert_data["content"] == [{"type": "text", "text": "plain text"}]


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
