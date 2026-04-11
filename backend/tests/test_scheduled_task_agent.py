"""ScheduledTaskAgent 单元测试

测试范围：
- 文件解析（_extract_files 从 [FILE] 标记提取）
- 上下文构建（_build_light_context）
- 摘要生成
- 完整执行流程（mock LLM adapter）
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.agent.scheduled_task_agent import (
    ScheduledTaskAgent,
    ScheduledTaskResult,
    _FILE_MARKER_RE,
)


# ════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════

def make_task(**overrides) -> dict:
    """构造测试任务字典"""
    base = {
        "id": "task_001",
        "user_id": "user_zhangsan",
        "org_id": "org_lanchuang",
        "name": "每日销售日报",
        "prompt": "查询昨日各店铺销售数据并生成汇总",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "push_target": {"type": "wecom_group", "chatid": "xxx"},
        "template_file": None,
        "max_credits": 10,
        "retry_count": 1,
        "timeout_sec": 180,
        "last_summary": None,
        "run_count": 0,
        "consecutive_failures": 0,
    }
    base.update(overrides)
    return base


# ════════════════════════════════════════════════════════
# 1. _extract_files 文件提取
# ════════════════════════════════════════════════════════

class TestExtractFiles:
    def test_single_file(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        text = (
            "执行完成。\n"
            "[FILE]https://cdn.example.com/report.xlsx|"
            "销售日报.xlsx|application/vnd.openxmlformats|12345[/FILE]"
        )
        files = agent._extract_files(text)
        assert len(files) == 1
        assert files[0]["url"] == "https://cdn.example.com/report.xlsx"
        assert files[0]["name"] == "销售日报.xlsx"
        assert files[0]["size"] == 12345

    def test_multiple_files(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        text = (
            "[FILE]https://a.com/1.xlsx|file1.xlsx|app/xlsx|100[/FILE]\n"
            "中间一些其他文字\n"
            "[FILE]https://b.com/2.csv|file2.csv|text/csv|200[/FILE]"
        )
        files = agent._extract_files(text)
        assert len(files) == 2
        assert files[0]["name"] == "file1.xlsx"
        assert files[1]["name"] == "file2.csv"

    def test_no_file(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        files = agent._extract_files("普通文本，没有附件")
        assert files == []

    def test_empty_text(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        assert agent._extract_files("") == []
        assert agent._extract_files(None) == []


# ════════════════════════════════════════════════════════
# 2. _build_light_context 上下文构建
# ════════════════════════════════════════════════════════

class TestBuildLightContext:
    def test_basic_task(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        messages = agent._build_light_context()

        # 应该有 system + system + user
        assert len(messages) >= 3
        assert messages[0]["role"] == "system"
        assert "定时任务执行器" in messages[0]["content"]
        assert messages[-1]["role"] == "user"
        assert "查询昨日各店铺销售数据" in messages[-1]["content"]

    def test_with_template_file(self):
        task = make_task(template_file={
            "path": "uploads/template.xlsx",
            "name": "销售模板.xlsx",
            "url": "https://cdn.example.com/template.xlsx",
        })
        agent = ScheduledTaskAgent(MagicMock(), task)
        messages = agent._build_light_context()

        user_msg = messages[-1]["content"]
        assert "模板文件" in user_msg
        assert "销售模板.xlsx" in user_msg
        assert "pd.read_excel" in user_msg
        assert "OUTPUT_DIR" in user_msg

    def test_with_last_summary(self):
        task = make_task(last_summary="昨日总销售额 12.5 万，比前日增长 8%")
        agent = ScheduledTaskAgent(MagicMock(), task)
        messages = agent._build_light_context()

        user_msg = messages[-1]["content"]
        assert "上次执行摘要" in user_msg
        assert "昨日总销售额 12.5 万" in user_msg

    def test_no_ask_user_instruction(self):
        """指令明确禁止 ask_user（无人交互场景）"""
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        messages = agent._build_light_context()
        assert "不要使用 ask_user" in messages[0]["content"]


# ════════════════════════════════════════════════════════
# 3. _generate_summary 摘要生成
# ════════════════════════════════════════════════════════

class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_short_text_returned_as_is(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        text = "短文本，不需要摘要"
        adapter = MagicMock()
        summary = await agent._generate_summary(text, adapter)
        assert summary == text

    @pytest.mark.asyncio
    async def test_long_text_calls_llm(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        text = "x" * 1000  # 长文本

        # mock adapter.stream_chat
        async def fake_stream(messages, **kwargs):
            class Chunk:
                def __init__(self, c):
                    self.content = c
            yield Chunk("摘要：测试结果")

        adapter = MagicMock()
        adapter.stream_chat = fake_stream

        summary = await agent._generate_summary(text, adapter)
        assert summary == "摘要：测试结果"

    @pytest.mark.asyncio
    async def test_empty_text(self):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        adapter = MagicMock()
        assert await agent._generate_summary("", adapter) == ""


# ════════════════════════════════════════════════════════
# 4. ScheduledTaskResult dataclass
# ════════════════════════════════════════════════════════

class TestScheduledTaskResult:
    def test_default_values(self):
        result = ScheduledTaskResult(text="hello")
        assert result.text == "hello"
        assert result.summary == ""
        assert result.status == "success"
        assert result.tokens_used == 0
        assert result.tools_called == []
        assert result.files == []
        assert result.is_truncated is False

    def test_full_construction(self):
        result = ScheduledTaskResult(
            text="完成",
            summary="销售额 10w",
            status="success",
            tokens_used=1500,
            turns_used=3,
            tools_called=["erp_agent", "code_execute"],
            files=[{"url": "https://x.com/a.xlsx", "name": "a.xlsx", "mime": "x", "size": 100}],
        )
        assert result.tokens_used == 1500
        assert len(result.files) == 1


# ════════════════════════════════════════════════════════
# 5. _FILE_MARKER_RE 正则
# ════════════════════════════════════════════════════════

class TestFileMarkerRegex:
    def test_matches_standard_format(self):
        text = "[FILE]https://cdn.example.com/report.xlsx|销售日报.xlsx|application/vnd|12345[/FILE]"
        match = _FILE_MARKER_RE.search(text)
        assert match
        assert match.group("url") == "https://cdn.example.com/report.xlsx"
        assert match.group("name") == "销售日报.xlsx"
        assert match.group("size") == "12345"

    def test_no_match_invalid(self):
        # 缺少字段
        assert not _FILE_MARKER_RE.search("[FILE]incomplete[/FILE]")
        # 没有标记
        assert not _FILE_MARKER_RE.search("plain text")
