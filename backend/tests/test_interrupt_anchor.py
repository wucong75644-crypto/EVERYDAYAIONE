"""interrupt_anchor 单元测试

覆盖 orphan tool_call 检测与补对逻辑。
详见 docs/document/TECH_用户中断与恢复机制.md §八.1
"""

from unittest.mock import MagicMock

import pytest

from services.handlers.interrupt_anchor import (
    INTERRUPTED_TOOL_RESULT,
    _append_partial_block,
    _mark_running_tools_cancelled,
    find_orphan_tool_calls,
    fix_orphan_tool_calls,
    persist_interrupt_anchor,
    reconcile_interrupted_messages,
)


def _assistant_with_tools(tool_calls):
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def _tc(tc_id, name, args=""):
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _tool_result(tc_id, content="ok"):
    return {"role": "tool", "tool_call_id": tc_id, "content": content}


class TestFindOrphanToolCalls:
    """find_orphan_tool_calls — 落锚阶段扫描内存 messages"""

    def test_no_tool_calls(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert find_orphan_tool_calls(msgs) == []

    def test_all_paired(self):
        msgs = [
            _assistant_with_tools([_tc("call_A", "search")]),
            _tool_result("call_A", "result"),
        ]
        assert find_orphan_tool_calls(msgs) == []

    def test_single_orphan(self):
        msgs = [_assistant_with_tools([_tc("call_A", "search")])]
        result = find_orphan_tool_calls(msgs)
        assert result == [("call_A", "search")]

    def test_multi_tool_partial_orphan(self):
        msgs = [
            _assistant_with_tools([
                _tc("call_A", "search"),
                _tc("call_B", "fetch"),
            ]),
            _tool_result("call_A"),
        ]
        result = find_orphan_tool_calls(msgs)
        assert result == [("call_B", "fetch")]

    def test_all_orphan(self):
        msgs = [
            _assistant_with_tools([
                _tc("call_A", "search"),
                _tc("call_B", "fetch"),
            ]),
        ]
        result = find_orphan_tool_calls(msgs)
        assert set(result) == {("call_A", "search"), ("call_B", "fetch")}

    def test_skip_invalid_tc(self):
        msgs = [
            _assistant_with_tools([
                {"id": "", "function": {"name": "x"}},
                {"id": "call_A", "function": {"name": ""}},
                _tc("call_B", "valid"),
            ]),
        ]
        result = find_orphan_tool_calls(msgs)
        assert result == [("call_B", "valid")]

    def test_empty_messages(self):
        assert find_orphan_tool_calls([]) == []


class TestFixOrphanToolCalls:
    """fix_orphan_tool_calls — history_loader 兜底"""

    def test_normal_paired_unchanged(self):
        msgs = [
            _assistant_with_tools([_tc("call_A", "search")]),
            _tool_result("call_A", "result"),
            {"role": "assistant", "content": "done"},
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 3
        assert result == msgs

    def test_single_orphan_inserted(self):
        msgs = [
            _assistant_with_tools([_tc("call_A", "search")]),
            {"role": "assistant", "content": "fallback text"},
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 3
        assert result[0] == msgs[0]
        assert result[1] == {
            "role": "tool",
            "tool_call_id": "call_A",
            "content": INTERRUPTED_TOOL_RESULT.format(tool_name="search"),
        }
        assert result[2] == msgs[1]

    def test_multi_tool_partial_missing(self):
        msgs = [
            _assistant_with_tools([
                _tc("call_A", "search"),
                _tc("call_B", "fetch"),
            ]),
            _tool_result("call_A", "result_A"),
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 3
        assert result[0] == msgs[0]
        assert result[1] == _tool_result("call_A", "result_A")
        assert result[2] == {
            "role": "tool",
            "tool_call_id": "call_B",
            "content": INTERRUPTED_TOOL_RESULT.format(tool_name="fetch"),
        }

    def test_all_orphan_inserted(self):
        msgs = [
            _assistant_with_tools([
                _tc("call_A", "search"),
                _tc("call_B", "fetch"),
            ]),
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 3
        synthetic_ids = {r["tool_call_id"] for r in result[1:]}
        assert synthetic_ids == {"call_A", "call_B"}
        for r in result[1:]:
            assert r["content"] == INTERRUPTED_TOOL_RESULT.format(
                tool_name="search" if r["tool_call_id"] == "call_A" else "fetch"
            )

    def test_out_of_order_tool_results_preserved(self):
        msgs = [
            _assistant_with_tools([
                _tc("call_A", "search"),
                _tc("call_B", "fetch"),
            ]),
            _tool_result("call_B", "result_B"),
            _tool_result("call_A", "result_A"),
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 3
        assert result == msgs

    def test_empty_messages(self):
        assert fix_orphan_tool_calls([]) == []

    def test_no_tool_calls(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert fix_orphan_tool_calls(msgs) == msgs

    def test_multiple_assistant_turns(self):
        msgs = [
            _assistant_with_tools([_tc("call_A", "search")]),
            _tool_result("call_A"),
            {"role": "user", "content": "next"},
            _assistant_with_tools([_tc("call_B", "fetch")]),
        ]
        result = fix_orphan_tool_calls(msgs)
        assert len(result) == 5
        assert result[4] == {
            "role": "tool",
            "tool_call_id": "call_B",
            "content": INTERRUPTED_TOOL_RESULT.format(tool_name="fetch"),
        }

    def test_does_not_drop_unknown_tool_messages(self):
        msgs = [
            _assistant_with_tools([_tc("call_A", "search")]),
            {"role": "tool", "tool_call_id": "call_X", "content": "stale"},
            _tool_result("call_A"),
        ]
        result = fix_orphan_tool_calls(msgs)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 2


class TestAppendPartialBlock:
    """_append_partial_block — partial 内容 dedup-aware 追加"""

    def test_empty_text_no_op(self):
        blocks = []
        _append_partial_block(blocks, "text", "")
        assert blocks == []

    def test_fresh_append(self):
        blocks = []
        _append_partial_block(blocks, "text", "hello")
        assert blocks == [{"type": "text", "text": "hello"}]

    def test_dedup_committed_full(self):
        blocks = [{"type": "text", "text": "hello"}]
        _append_partial_block(blocks, "text", "hello")
        assert len(blocks) == 1

    def test_append_delta(self):
        blocks = [{"type": "text", "text": "hello"}]
        _append_partial_block(blocks, "text", "hello world")
        assert len(blocks) == 2
        assert blocks[1] == {"type": "text", "text": " world"}

    def test_thinking_separate_from_text(self):
        blocks = [{"type": "text", "text": "abc"}]
        _append_partial_block(blocks, "thinking", "xyz")
        assert blocks == [
            {"type": "text", "text": "abc"},
            {"type": "thinking", "text": "xyz"},
        ]


class TestMarkRunningToolsCancelled:
    """_mark_running_tools_cancelled — running → cancelled 改写"""

    def test_no_running(self):
        blocks = [{"type": "tool_step", "status": "completed"}]
        count = _mark_running_tools_cancelled(blocks, "2026-06-05T00:00:00Z")
        assert count == 0
        assert blocks[0]["status"] == "completed"

    def test_running_to_cancelled(self):
        blocks = [
            {"type": "tool_step", "status": "running", "tool_name": "x"},
            {"type": "tool_step", "status": "completed", "tool_name": "y"},
            {"type": "tool_step", "status": "running", "tool_name": "z"},
        ]
        ts = "2026-06-05T00:00:00Z"
        count = _mark_running_tools_cancelled(blocks, ts)
        assert count == 2
        assert blocks[0]["status"] == "cancelled"
        assert blocks[0]["cancelled_at"] == ts
        assert blocks[1]["status"] == "completed"
        assert blocks[2]["status"] == "cancelled"
        assert blocks[2]["cancelled_at"] == ts

    def test_non_tool_step_blocks_skipped(self):
        blocks = [{"type": "text", "text": "abc"}]
        count = _mark_running_tools_cancelled(blocks, "ts")
        assert count == 0


class TestPersistInterruptAnchor:
    """persist_interrupt_anchor — 落锚原子操作核心"""

    @pytest.mark.asyncio
    async def test_basic_interrupt_marker_appended(self):
        db = MagicMock()
        messages = []
        content_blocks = []

        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id="org_x",
            messages=messages, content_blocks=content_blocks,
        )

        assert content_blocks[-1]["type"] == "interrupt_marker"
        assert content_blocks[-1]["reason"] == "user_cancel"
        assert "interrupted_at" in content_blocks[-1]

    @pytest.mark.asyncio
    async def test_partial_text_appended_before_marker(self):
        db = MagicMock()
        content_blocks = []

        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id=None,
            messages=[], content_blocks=content_blocks,
            partial_text="hello",
        )

        text_blocks = [b for b in content_blocks if b.get("type") == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["text"] == "hello"
        assert content_blocks[-1]["type"] == "interrupt_marker"

    @pytest.mark.asyncio
    async def test_partial_thinking_appended(self):
        db = MagicMock()
        content_blocks = []

        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id=None,
            messages=[], content_blocks=content_blocks,
            partial_thinking="思考中",
        )

        thinking_blocks = [b for b in content_blocks if b.get("type") == "thinking"]
        assert len(thinking_blocks) == 1
        assert thinking_blocks[0]["text"] == "思考中"

    @pytest.mark.asyncio
    async def test_orphan_补对_in_messages(self):
        db = MagicMock()
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_with_tools([_tc("call_A", "erp_query")]),
        ]
        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id=None,
            messages=messages, content_blocks=[],
        )

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_A"
        assert "erp_query" in tool_msgs[0]["content"]
        assert "用户在工具" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_running_tool_step_marked_cancelled(self):
        db = MagicMock()
        content_blocks = [
            {"type": "tool_step", "status": "running", "tool_name": "erp_query", "tool_call_id": "x"},
        ]
        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id=None,
            messages=[], content_blocks=content_blocks,
        )

        assert content_blocks[0]["status"] == "cancelled"
        assert "cancelled_at" in content_blocks[0]

    @pytest.mark.asyncio
    async def test_db_writes_called(self):
        db = MagicMock()
        await persist_interrupt_anchor(
            db=db, task_id="task_A", message_id="msg_A", org_id="org_x",
            messages=[], content_blocks=[],
        )

        table_calls = [c.args[0] for c in db.table.call_args_list]
        assert "messages" in table_calls
        assert "tasks" in table_calls

    @pytest.mark.asyncio
    async def test_tasks_write_failure_silent(self, caplog):
        db = MagicMock()
        original_table = db.table

        def selective_table(name):
            if name == "tasks":
                mock_chain = MagicMock()
                mock_chain.update.return_value.eq.return_value.execute.side_effect = Exception("net")
                return mock_chain
            return original_table(name)

        db.table = selective_table

        await persist_interrupt_anchor(
            db=db, task_id="t", message_id="m", org_id=None,
            messages=[], content_blocks=[],
        )


class TestReconcileInterruptedMessages:
    """reconcile_interrupted_messages — 自愈机制"""

    def _make_db(self, interrupted_msgs, task_lookups):
        """构造一个简化的 mock db。

        interrupted_msgs: list[dict] - messages 表的返回行
        task_lookups: dict[msg_id, list[dict]] - 按 msg_id 索引的 task 返回
        """
        db = MagicMock()
        update_calls = []

        class MsgChain:
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def gte(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            def execute(self):
                result = MagicMock()
                result.data = interrupted_msgs
                return result

        class TaskSelectChain:
            def __init__(self, msg_id):
                self.msg_id = msg_id
            def execute(self):
                result = MagicMock()
                result.data = task_lookups.get(self.msg_id, [])
                return result

        class TaskUpdateChain:
            def __init__(self, payload):
                self.payload = payload
            def eq(self, field, value):
                update_calls.append((value, self.payload))
                mock = MagicMock()
                mock.execute.return_value = MagicMock(data=[])
                return mock

        class TaskChain:
            def __init__(self):
                self._eq_value = None
            def select(self, *a, **kw):
                return self
            def eq(self, field, value):
                self._eq_value = value
                return TaskSelectChain(value)
            def update(self, payload):
                return TaskUpdateChain(payload)

        def table_dispatch(name):
            if name == "messages":
                return MsgChain()
            elif name == "tasks":
                return TaskChain()
            raise ValueError(name)

        db.table = table_dispatch
        db.update_calls = update_calls
        return db

    @pytest.mark.asyncio
    async def test_no_interrupted_messages(self):
        db = self._make_db([], {})
        result = await reconcile_interrupted_messages(db)
        assert result == {"scanned": 0, "reconciled": 0}

    @pytest.mark.asyncio
    async def test_already_consistent_skipped(self):
        msg_id = "msg_A"
        msg_content = [{"type": "text", "text": "hi"}]
        db = self._make_db(
            interrupted_msgs=[{"id": msg_id, "content": msg_content, "conversation_id": "c"}],
            task_lookups={
                msg_id: [{
                    "external_task_id": "task_A",
                    "accumulated_blocks": msg_content,
                    "status": "cancelled",
                }]
            },
        )
        result = await reconcile_interrupted_messages(db)
        assert result == {"scanned": 1, "reconciled": 0}
        assert db.update_calls == []

    @pytest.mark.asyncio
    async def test_inconsistent_reconciled(self):
        msg_id = "msg_A"
        msg_content = [{"type": "text", "text": "正确"}]
        db = self._make_db(
            interrupted_msgs=[{"id": msg_id, "content": msg_content, "conversation_id": "c"}],
            task_lookups={
                msg_id: [{
                    "external_task_id": "task_A",
                    "accumulated_blocks": [{"type": "text", "text": "陈旧"}],
                    "status": "running",
                }]
            },
        )
        result = await reconcile_interrupted_messages(db)
        assert result == {"scanned": 1, "reconciled": 1}
        assert len(db.update_calls) == 1
        assert db.update_calls[0][0] == "task_A"
        assert db.update_calls[0][1]["accumulated_blocks"] == msg_content
        assert db.update_calls[0][1]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_msg_without_task_skipped(self):
        db = self._make_db(
            interrupted_msgs=[{"id": "msg_orphan", "content": [], "conversation_id": "c"}],
            task_lookups={},
        )
        result = await reconcile_interrupted_messages(db)
        assert result == {"scanned": 1, "reconciled": 0}
