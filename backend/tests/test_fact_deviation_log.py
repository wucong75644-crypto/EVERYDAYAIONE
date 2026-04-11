"""L5 fact_deviation_log 测试 — 验证 DB 双写 + loguru 结构化日志。

设计文档：docs/document/TECH_ERP时间准确性架构.md §14.2
"""

import asyncio
from datetime import date
from unittest.mock import MagicMock

import pytest

from services.agent.guardrails.fact_deviation_log import emit_deviation_records
from services.agent.guardrails.temporal_validator import TemporalDeviation


def _make_dev(
    claimed: str = "周四",
    actual: str = "周五",
    date_str: str = "2026-04-03",
    snippet: str = "4月3日（上周四）",
) -> TemporalDeviation:
    return TemporalDeviation(
        date_str=date_str,
        parsed_date=date(2026, 4, 3),
        claimed_weekday=claimed,
        actual_weekday=actual,
        snippet=snippet,
        snippet_start=0,
        snippet_end=len(snippet),
    )


class TestEmitDeviationRecords:
    """emit_deviation_records 行为测试。"""

    @pytest.mark.asyncio
    async def test_empty_deviations_noop(self):
        """空偏离列表不写任何东西。"""
        db = MagicMock()
        emit_deviation_records(
            db=db, deviations=[], task_id="t", conversation_id="c",
            user_id="u", org_id="o", turn=1, patched=True,
        )
        # give event loop a chance to run any fire-and-forget tasks
        await asyncio.sleep(0)
        db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_deviation_writes_audit_log(self):
        """单个偏离写一条 tool_audit_log 记录。"""
        db = MagicMock()
        # Make insert().execute() chain return something
        db.table.return_value.insert.return_value.execute.return_value = None

        dev = _make_dev()
        emit_deviation_records(
            db=db, deviations=[dev], task_id="task_x",
            conversation_id="conv_x", user_id="user_x",
            org_id="org_x", turn=3, patched=True,
        )
        # Wait for fire-and-forget task
        await asyncio.sleep(0.05)

        # 验证写入了 tool_audit_log
        assert db.table.called
        args, _ = db.table.call_args
        assert args[0] == "tool_audit_log"

        # 验证 insert 的 row 内容
        insert_args, _ = db.table.return_value.insert.call_args
        row = insert_args[0]
        assert row["tool_name"] == "temporal_validator"
        assert row["status"] == "auto_patched"
        assert row["task_id"] == "task_x"
        assert row["conversation_id"] == "conv_x"
        assert row["user_id"] == "user_x"
        assert row["org_id"] == "org_x"
        assert row["turn"] == 3
        assert row["result_length"] == len(dev.snippet)
        assert row["tool_call_id"] == "l4_patch_3_0"

    @pytest.mark.asyncio
    async def test_multiple_deviations_multiple_writes(self):
        """多个偏离写多条记录。"""
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        devs = [
            _make_dev(claimed="周四", actual="周五", snippet="snippet_1"),
            _make_dev(claimed="周三", actual="周二", snippet="snippet_2"),
        ]
        emit_deviation_records(
            db=db, deviations=devs, task_id="t", conversation_id="c",
            user_id="u", org_id="o", turn=1, patched=True,
        )
        await asyncio.sleep(0.05)

        assert db.table.call_count == 2
        # 两次 insert 的 tool_call_id 应递增
        inserts = [
            call.args[0] for call in db.table.return_value.insert.call_args_list
        ]
        assert inserts[0]["tool_call_id"] == "l4_patch_1_0"
        assert inserts[1]["tool_call_id"] == "l4_patch_1_1"

    @pytest.mark.asyncio
    async def test_not_patched_status(self):
        """patched=False 时状态为 deviation_detected。"""
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        dev = _make_dev()
        emit_deviation_records(
            db=db, deviations=[dev], task_id="t", conversation_id="c",
            user_id="u", org_id="o", turn=1, patched=False,
        )
        await asyncio.sleep(0.05)

        row = db.table.return_value.insert.call_args.args[0]
        assert row["status"] == "deviation_detected"

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self):
        """DB 写入失败不抛异常（fire-and-forget）。"""
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.side_effect = (
            Exception("DB down")
        )

        dev = _make_dev()
        # 不应抛异常
        emit_deviation_records(
            db=db, deviations=[dev], task_id="t", conversation_id="c",
            user_id="u", org_id="o", turn=1, patched=True,
        )
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_loguru_binding_captured(self, caplog):
        """loguru 结构化字段记录成功（通过 logger 调用验证）。"""
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        # 捕获 loguru 日志到 caplog（用 propagate hack）
        import logging
        from loguru import logger as _logger

        handler_id = _logger.add(
            lambda msg: logging.getLogger("loguru_test").warning(msg),
            level="WARNING",
        )
        try:
            with caplog.at_level(logging.WARNING, logger="loguru_test"):
                dev = _make_dev()
                emit_deviation_records(
                    db=db, deviations=[dev], task_id="t",
                    conversation_id="c", user_id="u",
                    org_id="o", turn=1, patched=True,
                )
                await asyncio.sleep(0.05)

            # 验证日志被触发（内容含 L5 标记）
            assert any("L5" in rec.message for rec in caplog.records), \
                "L5 日志未触发"
        finally:
            _logger.remove(handler_id)
