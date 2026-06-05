"""cancel_metrics 单元测试（Phase 2）

覆盖：
- mark_cancel_start + record_cancel_latency 配对计算
- record_cancel_event 基础输出
- 未配对调用的优雅降级（不抛错）

设计参考 docs/document/TECH_用户中断与恢复机制.md §十二
"""

import time

import pytest
from loguru import logger as loguru_logger

from services import cancel_metrics


@pytest.fixture(autouse=True)
def _clear_state():
    """每个测试前清理 module-level 状态，避免污染"""
    cancel_metrics._cancel_started_at.clear()
    yield
    cancel_metrics._cancel_started_at.clear()


@pytest.fixture
def captured_logs():
    """捕获 loguru 日志到列表"""
    records = []
    sink_id = loguru_logger.add(
        lambda msg: records.append(msg.record["message"]),
        level="DEBUG",
    )
    yield records
    loguru_logger.remove(sink_id)


class TestMarkCancelStart:
    def test_mark_writes_state(self):
        cancel_metrics.mark_cancel_start("task_A")
        assert "task_A" in cancel_metrics._cancel_started_at
        assert isinstance(cancel_metrics._cancel_started_at["task_A"], float)

    def test_mark_overwrites_prior(self):
        cancel_metrics.mark_cancel_start("task_A")
        first = cancel_metrics._cancel_started_at["task_A"]
        time.sleep(0.01)
        cancel_metrics.mark_cancel_start("task_A")
        assert cancel_metrics._cancel_started_at["task_A"] > first


class TestRecordCancelLatency:
    def test_latency_after_mark(self, captured_logs):
        cancel_metrics.mark_cancel_start("task_A")
        time.sleep(0.02)
        cancel_metrics.record_cancel_latency("task_A", org_id="org_x", phase="stream")

        assert "task_A" not in cancel_metrics._cancel_started_at
        latency_logs = [m for m in captured_logs if "gen_ai.cancel.latency" in m]
        assert len(latency_logs) == 1
        assert "task=task_A" in latency_logs[0]
        assert "org=org_x" in latency_logs[0]
        assert "phase=stream" in latency_logs[0]
        assert "latency_ms=" in latency_logs[0]

    def test_latency_without_mark_silent(self, captured_logs):
        cancel_metrics.record_cancel_latency("task_NEVER", "org_x")
        latency_logs = [m for m in captured_logs if "gen_ai.cancel.latency" in m]
        assert latency_logs == []

    def test_latency_includes_tags(self, captured_logs):
        cancel_metrics.mark_cancel_start("task_B")
        cancel_metrics.record_cancel_latency(
            "task_B", "org_x", phase="tool_pre",
            had_partial=True, tools_in_flight=2,
        )
        latency_logs = [m for m in captured_logs if "gen_ai.cancel.latency" in m]
        assert "had_partial=True" in latency_logs[0]
        assert "tools_in_flight=2" in latency_logs[0]


class TestRecordCancelEvent:
    def test_event_basic_output(self, captured_logs):
        cancel_metrics.record_cancel_event("task_A", org_id="org_x")
        event_logs = [m for m in captured_logs if "gen_ai.cancel.events" in m]
        assert len(event_logs) == 1
        assert "task=task_A" in event_logs[0]
        assert "source=frontend_button" in event_logs[0]

    def test_event_with_partial_and_tools(self, captured_logs):
        cancel_metrics.record_cancel_event(
            "task_A", "org_x", had_partial=True, tools_in_flight=3,
        )
        event_logs = [m for m in captured_logs if "gen_ai.cancel.events" in m]
        assert "had_partial=True" in event_logs[0]
        assert "tools_in_flight=3" in event_logs[0]


class TestRecordOrphanFixed:
    def test_zero_count_skipped(self, captured_logs):
        cancel_metrics.record_orphan_fixed("task_A", "org_x", fixed_count=0)
        orphan_logs = [m for m in captured_logs if "gen_ai.cancel.orphan_fixed" in m]
        assert orphan_logs == []

    def test_positive_count_logged(self, captured_logs):
        cancel_metrics.record_orphan_fixed("task_A", "org_x", fixed_count=2)
        orphan_logs = [m for m in captured_logs if "gen_ai.cancel.orphan_fixed" in m]
        assert len(orphan_logs) == 1
        assert "fixed_count=2" in orphan_logs[0]


class TestRecordContinued5m:
    def test_basic_output(self, captured_logs):
        cancel_metrics.record_continued_5m("task_A", "org_x")
        cont_logs = [m for m in captured_logs if "gen_ai.cancel.continued_5m" in m]
        assert len(cont_logs) == 1
        assert "task=task_A" in cont_logs[0]
