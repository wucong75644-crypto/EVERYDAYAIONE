"""cancel API 立即落锚 messages 表的单测

修复 race condition：用户在 chat_handler 后台落锚前秒发"继续"，
导致 history_loader 检测不到 interrupt_marker → LLM 失忆。

详见 docs/document/TECH_用户中断与恢复机制.md §四.2
"""

import json
from unittest.mock import MagicMock

from api.routes.task import _anchor_messages_immediately


def _make_db(existing_content):
    """构造 mock db: select messages.content + update messages + upsert messages"""
    db = MagicMock()
    db.update_payloads = []
    db.upsert_payloads = []

    select_chain = MagicMock()
    select_result = MagicMock()
    select_result.data = (
        [{"content": existing_content}] if existing_content is not None else []
    )
    select_chain.eq.return_value.execute.return_value = select_result

    def capture_update(payload):
        db.update_payloads.append(payload)
        mock = MagicMock()
        mock.eq.return_value.execute.return_value = MagicMock(data=[])
        return mock

    def capture_upsert(payload, on_conflict=""):
        db.upsert_payloads.append({"data": payload, "on_conflict": on_conflict})
        mock = MagicMock()
        mock.execute.return_value = MagicMock(data=[payload])
        return mock

    msg_table = MagicMock()
    msg_table.select.return_value = select_chain
    msg_table.update = capture_update
    msg_table.upsert = capture_upsert

    db.table = lambda name: msg_table
    return db


class TestAnchorImmediately:
    def test_empty_content_appends_marker(self):
        db = _make_db([])
        _anchor_messages_immediately(db, "msg_X")
        assert len(db.update_payloads) == 1
        payload = db.update_payloads[0]
        assert payload["status"] == "interrupted"
        assert len(payload["content"]) == 1
        assert payload["content"][0]["type"] == "interrupt_marker"
        assert payload["content"][0]["reason"] == "user_cancel"

    def test_running_tool_step_marked_cancelled(self):
        db = _make_db([
            {"type": "text", "text": "正在查"},
            {
                "type": "tool_step",
                "tool_name": "file_analyze",
                "tool_call_id": "call_A",
                "status": "running",
            },
        ])
        _anchor_messages_immediately(db, "msg_X")
        payload = db.update_payloads[0]
        tool_step = next(b for b in payload["content"] if b.get("type") == "tool_step")
        assert tool_step["status"] == "cancelled"
        assert "cancelled_at" in tool_step

    def test_already_completed_tool_step_unchanged(self):
        db = _make_db([
            {
                "type": "tool_step",
                "tool_name": "x",
                "tool_call_id": "call_B",
                "status": "completed",
                "output": "result",
            },
        ])
        _anchor_messages_immediately(db, "msg_X")
        payload = db.update_payloads[0]
        tool_step = next(b for b in payload["content"] if b.get("type") == "tool_step")
        assert tool_step["status"] == "completed"  # 不变

    def test_existing_marker_not_duplicated(self):
        db = _make_db([
            {"type": "text", "text": "hi"},
            {
                "type": "interrupt_marker",
                "interrupted_at": "2026-06-05T14:30:00+08:00",
                "reason": "user_cancel",
            },
        ])
        _anchor_messages_immediately(db, "msg_X")
        payload = db.update_payloads[0]
        markers = [b for b in payload["content"] if b.get("type") == "interrupt_marker"]
        assert len(markers) == 1

    def test_json_string_content_parsed(self):
        db = _make_db(json.dumps([{"type": "text", "text": "hi"}]))
        _anchor_messages_immediately(db, "msg_X")
        payload = db.update_payloads[0]
        assert payload["status"] == "interrupted"
        assert any(b.get("type") == "interrupt_marker" for b in payload["content"])

    def test_message_not_found_without_conv_id_skipped(self):
        """message 不存在 + 没传 conversation_id → 不报错也不写"""
        db = _make_db(None)
        _anchor_messages_immediately(db, "msg_NONEXISTENT")
        assert db.update_payloads == []
        assert db.upsert_payloads == []

    def test_message_not_found_with_conv_id_creates_stub(self):
        """message 不存在 + 有 conversation_id → upsert 创建 stub message

        修复 chat lazy 创建场景：chat 任务的 message 在 on_complete 才入库，
        cancel 路径跳过了 _upsert，message 从未存在 → 必须主动创建 stub
        否则 history_loader 加载不到中断标记 → LLM 失忆。
        """
        db = _make_db(None)
        _anchor_messages_immediately(
            db, "msg_NEW", conversation_id="conv_X",
        )
        assert len(db.upsert_payloads) == 1
        payload = db.upsert_payloads[0]
        assert payload["on_conflict"] == "id"
        data = payload["data"]
        assert data["id"] == "msg_NEW"
        assert data["conversation_id"] == "conv_X"
        assert data["role"] == "assistant"
        assert data["status"] == "interrupted"
        # content 末尾应有 interrupt_marker
        assert any(
            b.get("type") == "interrupt_marker" for b in data["content"]
        )

    def test_db_failure_silent(self, caplog):
        db = MagicMock()
        db.table.side_effect = Exception("network")
        _anchor_messages_immediately(db, "msg_X")
