"""
tool_audit 单元测试

覆盖：ToolAuditEntry / build_args_hash / record_tool_audit
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.agent.tool_audit import (
    ToolAuditEntry,
    build_args_hash,
    record_tool_audit,
)


class TestToolAuditEntry:

    def test_default_fields(self):
        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="local_stock_query", tool_call_id="tc1",
            turn=1, args_hash="abc123", result_length=500,
            elapsed_ms=45, status="success",
        )
        assert entry.is_cached is False
        assert entry.is_truncated is False
        assert entry.status == "success"

    def test_all_fields(self):
        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="erp_trade_query", tool_call_id="tc2",
            turn=3, args_hash="def456", result_length=3000,
            elapsed_ms=230, status="timeout",
            is_cached=False, is_truncated=True,
        )
        assert entry.status == "timeout"
        assert entry.is_truncated is True


class TestBuildArgsHash:

    def test_deterministic(self):
        h1 = build_args_hash({"b": 2, "a": 1})
        h2 = build_args_hash({"a": 1, "b": 2})
        assert h1 == h2  # sort_keys=True

    def test_different_args_different_hash(self):
        h1 = build_args_hash({"action": "get_orders"})
        h2 = build_args_hash({"action": "get_products"})
        assert h1 != h2

    def test_empty_args(self):
        h = build_args_hash({})
        assert isinstance(h, str)
        assert len(h) == 12  # MD5[:12]

    def test_chinese_args(self):
        h = build_args_hash({"keyword": "连衣裙"})
        assert isinstance(h, str)


class TestRecordToolAudit:

    @pytest.mark.asyncio
    async def test_success_write(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = None

        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="local_stock_query", tool_call_id="tc1",
            turn=1, args_hash="abc", result_length=100,
            elapsed_ms=10, status="success",
        )
        await record_tool_audit(mock_db, entry)

        mock_db.table.assert_called_once_with("tool_audit_log")
        insert_call = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_call["tool_name"] == "local_stock_query"
        assert insert_call["status"] == "success"
        assert insert_call["elapsed_ms"] == 10

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self):
        """DB 写入失败不应抛异常（fire-and-forget 安全）"""
        mock_db = MagicMock()
        mock_db.table.side_effect = Exception("DB connection lost")

        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="tool", tool_call_id="tc1",
            turn=1, args_hash="abc", result_length=0,
            elapsed_ms=0, status="error",
        )
        # 不应抛异常
        await record_tool_audit(mock_db, entry)

    @pytest.mark.asyncio
    async def test_error_entry_fields(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = None

        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="erp_execute", tool_call_id="tc2",
            turn=2, args_hash="xyz", result_length=50,
            elapsed_ms=5000, status="timeout",
            is_cached=False, is_truncated=False,
        )
        await record_tool_audit(mock_db, entry)

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["status"] == "timeout"
        assert row["elapsed_ms"] == 5000

    @pytest.mark.asyncio
    async def test_cached_entry(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = None

        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="local_stock_query", tool_call_id="tc3",
            turn=1, args_hash="abc", result_length=200,
            elapsed_ms=0, status="success",
            is_cached=True,
        )
        await record_tool_audit(mock_db, entry)

        row = mock_db.table.return_value.insert.call_args[0][0]
        assert row["is_cached"] is True
        assert row["elapsed_ms"] == 0

    @pytest.mark.asyncio
    async def test_to_thread_actually_calls_db(self):
        """asyncio.to_thread 正确执行 lambda 闭包中的 DB 调用"""
        call_log = []

        class FakeExecute:
            def execute(self):
                call_log.append("executed")

        class FakeInsert:
            def insert(self, row):
                call_log.append(("insert", row["tool_name"]))
                return FakeExecute()

        class FakeDB:
            def table(self, name):
                call_log.append(("table", name))
                return FakeInsert()

        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="local_stock_query", tool_call_id="tc1",
            turn=1, args_hash="abc", result_length=100,
            elapsed_ms=10, status="success",
        )
        await record_tool_audit(FakeDB(), entry)

        assert ("table", "tool_audit_log") in call_log
        assert ("insert", "local_stock_query") in call_log
        assert "executed" in call_log
