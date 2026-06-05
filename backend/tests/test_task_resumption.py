"""Phase 4 测试：TASK RESUMPTION 注入 + cancelled 状态 + format_relative_time

详见 docs/document/TECH_用户中断与恢复机制.md §四.4 / §15.5
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.handlers.chat_context.content_extractors import (
    extract_interrupt_marker,
    extract_oai_messages_from_content,
)
from services.handlers.interrupt_anchor import (
    INTERRUPTED_TOOL_RESULT,
    TASK_RESUMPTION_TEMPLATE,
)
from utils.time_context import format_relative_time, now_cn


class TestFormatRelativeTime:
    def test_just_now(self):
        assert format_relative_time(now_cn()) == "刚刚"

    def test_minutes_ago(self):
        five_min_ago = now_cn() - timedelta(minutes=5)
        assert format_relative_time(five_min_ago) == "约 5 分钟前"

    def test_hours_ago(self):
        two_hours_ago = now_cn() - timedelta(hours=2)
        assert format_relative_time(two_hours_ago) == "约 2 小时前"

    def test_days_ago(self):
        three_days_ago = now_cn() - timedelta(days=3)
        assert format_relative_time(three_days_ago) == "约 3 天前"

    def test_iso_string_input(self):
        iso = (now_cn() - timedelta(minutes=10)).isoformat()
        assert format_relative_time(iso) == "约 10 分钟前"

    def test_invalid_input_safe(self):
        assert format_relative_time("not-a-date") == "未知时间前"


class TestExtractInterruptMarker:
    def test_no_marker(self):
        content = [{"type": "text", "text": "hi"}]
        assert extract_interrupt_marker(content) is None

    def test_marker_present(self):
        content = [
            {"type": "text", "text": "hi"},
            {
                "type": "interrupt_marker",
                "interrupted_at": "2026-06-05T14:30:00+08:00",
                "reason": "user_cancel",
            },
        ]
        marker = extract_interrupt_marker(content)
        assert marker is not None
        assert marker["reason"] == "user_cancel"

    def test_string_json_input(self):
        content = json.dumps([
            {"type": "interrupt_marker", "interrupted_at": "2026-01-01T00:00:00Z",
             "reason": "user_cancel"},
        ])
        marker = extract_interrupt_marker(content)
        assert marker is not None
        assert marker["reason"] == "user_cancel"

    def test_invalid_input_returns_none(self):
        assert extract_interrupt_marker("plain text") is None
        assert extract_interrupt_marker(None) is None
        assert extract_interrupt_marker({}) is None


class TestContentExtractorsCancelled:
    """content_extractors 处理 tool_step.status='cancelled'"""

    def test_cancelled_generates_synthetic_tool_result(self):
        content = [
            {
                "type": "tool_step",
                "tool_name": "erp_query",
                "tool_call_id": "call_A",
                "status": "cancelled",
                "input": "{}",
                "cancelled_at": "2026-06-05T14:30:00+08:00",
            },
        ]
        msgs = extract_oai_messages_from_content(content, role="assistant")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["tool_calls"][0]["id"] == "call_A"
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "call_A"
        assert msgs[1]["content"] == INTERRUPTED_TOOL_RESULT.format(tool_name="erp_query")

    def test_running_still_skipped(self):
        """status='running' 仍然跳过（未完成的工具不生成 LLM 历史）"""
        content = [
            {
                "type": "tool_step",
                "tool_name": "x",
                "tool_call_id": "call_X",
                "status": "running",
            },
        ]
        msgs = extract_oai_messages_from_content(content, role="assistant")
        assert msgs == []

    def test_mixed_completed_and_cancelled(self):
        content = [
            {"type": "tool_step", "tool_name": "a", "tool_call_id": "1",
             "status": "completed", "output": "result_a"},
            {"type": "tool_step", "tool_name": "b", "tool_call_id": "2",
             "status": "cancelled"},
        ]
        msgs = extract_oai_messages_from_content(content, role="assistant")
        assert len(msgs) == 4
        assert msgs[1]["content"] == "result_a"
        assert "用户在工具 'b'" in msgs[3]["content"]


class TestTaskResumptionTemplate:
    def test_template_format(self):
        rendered = TASK_RESUMPTION_TEMPLATE.format(ago_text="约 5 分钟前")
        assert "[任务恢复]" in rendered
        assert "约 5 分钟前" in rendered
        assert "工具" in rendered
        assert "重试" in rendered
