"""ScheduledTaskAgent execute() 完整流程集成测试

mock LLM adapter / ToolExecutor / phase_tools，测试：
- 单轮直接回复（无工具调用）
- 多轮工具调用（erp_agent → code_execute → 最终结果）
- 循环检测（连续 3 次相同调用）
- Token 预算超限
- 时间预算超限
- 上下文超限恢复
- 异常路径（adapter 抛错）
- 文件提取（沙盒输出 [FILE] 标记）
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from typing import List, Optional

from services.agent.scheduled_task_agent import (
    ScheduledTaskAgent,
    ScheduledTaskResult,
    MAX_SCHEDULED_TURNS,
)


# ════════════════════════════════════════════════════════
# Fake LLM Chunk / Adapter
# ════════════════════════════════════════════════════════

@dataclass
class FakeToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments_delta: str = ""


@dataclass
class FakeChunk:
    content: Optional[str] = None
    tool_calls: Optional[List[FakeToolCallDelta]] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class FakeAdapter:
    """模拟 LLM adapter

    用一个 turns 列表控制每轮返回什么：
    [
        {"text": "完成了销售日报", "tool_calls": []},  # 直接最终回复
        {"text": "我需要查数据", "tool_calls": [{"id": "c1", "name": "erp_agent", "args": '{"q":"销售"}'}]},
    ]
    """
    def __init__(self, turns: list):
        self.turns = turns
        self.call_count = 0
        self.closed = False

    async def close(self):
        self.closed = True

    async def stream_chat(self, messages=None, tools=None, temperature=None, **kwargs):
        if self.call_count >= len(self.turns):
            # 默认直接结束
            yield FakeChunk(content="默认结束", prompt_tokens=10, completion_tokens=5)
            return

        turn = self.turns[self.call_count]
        self.call_count += 1

        if turn.get("text"):
            yield FakeChunk(content=turn["text"])

        for tc in turn.get("tool_calls", []):
            yield FakeChunk(tool_calls=[
                FakeToolCallDelta(
                    index=0,
                    id=tc["id"],
                    name=tc["name"],
                    arguments_delta=tc["args"],
                )
            ])

        yield FakeChunk(
            prompt_tokens=turn.get("prompt_tokens", 100),
            completion_tokens=turn.get("completion_tokens", 50),
        )


class FakeToolExecutor:
    """模拟 ToolExecutor"""
    def __init__(self, results: dict | None = None):
        self.results = results or {}
        self.calls: list = []

    async def execute(self, tool_name: str, args: dict) -> str:
        self.calls.append((tool_name, args))
        if tool_name in self.results:
            r = self.results[tool_name]
            if isinstance(r, Exception):
                raise r
            return r
        return f"[mock {tool_name} result]"


# ════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════

def make_task(**overrides) -> dict:
    base = {
        "id": "task_int_001",
        "user_id": "user_zhangsan",
        "org_id": "org_lanchuang",
        "name": "测试任务",
        "prompt": "查询昨日销售数据",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "push_target": {"type": "wecom_group", "chatid": "x"},
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


def patch_agent_dependencies(adapter: FakeAdapter, executor: FakeToolExecutor):
    """patch ScheduledTaskAgent.execute 依赖的 4 个 import"""
    return [
        patch(
            "config.phase_tools.build_domain_tools",
            return_value=[],
        ),
        patch(
            "services.adapters.factory.create_chat_adapter",
            return_value=adapter,
        ),
        patch(
            "services.agent.tool_executor.ToolExecutor",
            return_value=executor,
        ),
    ]


# ════════════════════════════════════════════════════════
# 测试
# ════════════════════════════════════════════════════════

class TestExecuteHappyPath:

    @pytest.mark.asyncio
    async def test_single_turn_direct_reply(self):
        """force_tool_use_first=True: 首轮纯文本被忽略，连续2次空工具后采用文本"""
        adapter = FakeAdapter([
            {"text": "昨日销售总额 12.5 万元", "tool_calls": []},
            {"text": "昨日销售总额 12.5 万元", "tool_calls": []},
        ])
        executor = FakeToolExecutor()

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.status == "success"
        assert "12.5 万" in result.text
        assert result.tools_called == []
        assert result.tokens_used > 0
        assert adapter.closed is True

    @pytest.mark.asyncio
    async def test_multi_turn_tool_calling(self):
        """多轮工具调用：先调 erp_agent，再合成最终回复"""
        adapter = FakeAdapter([
            # 第 1 轮：调 erp_agent
            {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "erp_agent", "args": '{"query":"销售"}'}],
            },
            # 第 2 轮：基于工具结果合成最终回复
            {"text": "昨日销售: A店 5万, B店 7万", "tool_calls": []},
        ])
        executor = FakeToolExecutor(results={
            "erp_agent": "A店 5万, B店 7万",
        })

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.status == "success"
        assert "A店" in result.text
        assert result.tools_called == ["erp_agent"]
        assert result.turns_used == 2
        assert executor.calls[0][0] == "erp_agent"

    @pytest.mark.asyncio
    async def test_extract_files_from_sandbox_output(self):
        """工具结果中包含 [FILE] 标记 → 提取到 result.files"""
        sandbox_output = (
            "已生成文件\n"
            "[FILE]https://cdn.example.com/report.xlsx|销售日报.xlsx|"
            "application/vnd.openxmlformats|12345[/FILE]"
        )
        adapter = FakeAdapter([
            {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "code_execute", "args": "{}"}],
            },
            {"text": sandbox_output, "tool_calls": []},
        ])
        executor = FakeToolExecutor(results={"code_execute": sandbox_output})

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.status == "success"
        # 文件应该从最终 text 中提取
        assert len(result.files) == 1
        assert result.files[0]["name"] == "销售日报.xlsx"
        assert result.files[0]["url"] == "https://cdn.example.com/report.xlsx"


class TestExecuteSafetyGuards:

    @pytest.mark.asyncio
    async def test_loop_detection_breaks_after_3_same_calls(self):
        """连续 3 次相同工具调用 → 中止循环"""
        same_call = {"id": "c1", "name": "erp_agent", "args": '{"q":"x"}'}
        adapter = FakeAdapter([
            {"text": "", "tool_calls": [same_call]},
            {"text": "", "tool_calls": [same_call]},
            {"text": "", "tool_calls": [same_call]},
            {"text": "", "tool_calls": [same_call]},  # 第 4 轮不应该被调
        ])
        executor = FakeToolExecutor(results={"erp_agent": "结果"})

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        # 循环检测：3 次后中止
        # adapter.call_count 应该 ≤ 3（第 4 轮没被调）
        assert adapter.call_count <= 3
        assert result.status == "success"  # 仍然返回，只是 text 是最后一次工具结果

    @pytest.mark.asyncio
    async def test_max_turns_breaks_loop(self):
        """到达 MAX_SCHEDULED_TURNS 自动中止"""
        # 构造一直调工具但每次都不同的场景，绕过循环检测
        turns = [
            {
                "text": "",
                "tool_calls": [{
                    "id": f"c{i}",
                    "name": "erp_agent",
                    "args": f'{{"q":"q{i}"}}',
                }],
            }
            for i in range(MAX_SCHEDULED_TURNS + 5)
        ]
        adapter = FakeAdapter(turns)
        executor = FakeToolExecutor(results={"erp_agent": "结果"})

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.turns_used <= MAX_SCHEDULED_TURNS
        assert adapter.call_count == MAX_SCHEDULED_TURNS

    @pytest.mark.asyncio
    async def test_tool_exception_handled(self):
        """工具抛异常 → 继续循环（用错误结果作为 tool result）"""
        adapter = FakeAdapter([
            {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "erp_agent", "args": "{}"}],
            },
            {"text": "已知 ERP 出错，无法生成报告", "tool_calls": []},
        ])
        executor = FakeToolExecutor(results={
            "erp_agent": RuntimeError("ERP 接口超时"),
        })

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        # 工具异常不会让整个 Agent 失败 — Agent 会拿到错误 message 继续合成
        assert result.status == "success"
        assert "ERP 出错" in result.text or result.tools_called == ["erp_agent"]

    @pytest.mark.asyncio
    async def test_invalid_json_args_handled(self):
        """工具参数 JSON 错误 → 不崩溃"""
        adapter = FakeAdapter([
            {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "erp_agent", "args": "not-json"}],
            },
            {"text": "参数错误已记录", "tool_calls": []},
        ])
        executor = FakeToolExecutor()

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        # JSON 错误不应该让 Agent 崩溃
        assert result.status == "success"
        # executor 不应该被调用（因为 JSON 解析失败）
        assert len(executor.calls) == 0


class TestExecuteErrorPaths:

    @pytest.mark.asyncio
    async def test_adapter_creation_fails(self):
        """create_chat_adapter 抛异常 → status=error"""
        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch(
                 "services.adapters.factory.create_chat_adapter",
                 side_effect=RuntimeError("API key 失效"),
             ):
            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.status == "error"
        assert "API key" in result.error_message

    @pytest.mark.asyncio
    async def test_stream_chat_raises_propagates_to_error(self):
        """adapter.stream_chat 在循环中抛非上下文异常 → status=error"""
        class CrashAdapter:
            closed = False
            async def close(self): self.closed = True
            async def stream_chat(self, **kwargs):
                # 必须 yield 一次让它变成 async generator
                if False:
                    yield None
                raise RuntimeError("LLM 服务不可用")

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch(
                 "services.adapters.factory.create_chat_adapter",
                 return_value=CrashAdapter(),
             ), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=FakeToolExecutor()):
            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.status == "error"
        assert "LLM" in result.error_message or "服务" in result.error_message


class TestSummaryGeneration:

    @pytest.mark.asyncio
    async def test_short_text_uses_text_as_summary(self):
        """text 短于 500 字 → summary == text，不调 LLM 生成摘要"""
        adapter = FakeAdapter([
            {"text": "短文本结果", "tool_calls": []},
        ])
        executor = FakeToolExecutor()

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.summary == result.text

    @pytest.mark.asyncio
    async def test_long_text_triggers_summary_call(self):
        """text 超过 500 字 → 调用 LLM 生成摘要（force_tool_use_first 需连续2轮空工具）"""
        long_text = "数据" * 300  # 600 字
        adapter = FakeAdapter([
            {"text": long_text, "tool_calls": []},
            # 第2轮：force_tool_use_first 强制再来一轮，仍输出长文本
            {"text": long_text, "tool_calls": []},
            # 第3次调用是 _generate_summary
            {"text": "200 字摘要", "tool_calls": []},
        ])
        executor = FakeToolExecutor()

        with patch("config.phase_tools.build_domain_tools", return_value=[]), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("services.agent.tool_executor.ToolExecutor", return_value=executor):

            agent = ScheduledTaskAgent(MagicMock(), make_task())
            result = await agent.execute()

        assert result.summary == "200 字摘要"
        assert len(result.text) > 500


class TestTemplatePreparation:

    @pytest.mark.asyncio
    async def test_template_message_injected(self):
        """有 template_file 时，user message 包含模板提示"""
        task = make_task(template_file={
            "path": "uploads/tpl.xlsx",
            "name": "销售模板.xlsx",
            "url": "https://cdn.x.com/tpl.xlsx",
        })
        agent = ScheduledTaskAgent(MagicMock(), task)
        messages = agent._build_light_context()

        user_msg = messages[-1]["content"]
        assert "模板文件" in user_msg
        assert "销售模板.xlsx" in user_msg
        assert "pd.read_excel" in user_msg
