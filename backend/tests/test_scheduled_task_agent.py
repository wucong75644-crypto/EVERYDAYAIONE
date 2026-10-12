"""ScheduledTaskAgent 单元测试

测试范围：
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

from core.config import Settings
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from tests.tool_runtime_support import IdentityDB
from services.agent.scheduled_task_agent import (
    ScheduledTaskAgent,
    ScheduledTaskResult,
)
from services.agent.loop_types import LoopResult


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
        "execution_policy": {"version": 1, "allowed_tools": ["erp_agent"]},
    }
    base.update(overrides)
    return base


# ════════════════════════════════════════════════════════
# 1. _build_light_context 上下文构建
# ════════════════════════════════════════════════════════

class TestBuildLightContext:
    def test_basic_task(self):
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
        messages = agent._build_light_context()

        # 应该有 system + system + user
        assert len(messages) >= 3
        assert messages[0]["role"] == "system"
        assert "定时任务执行器" in messages[0]["content"]
        assert messages[-1]["role"] == "user"
        assert "查询昨日各店铺销售数据" in messages[-1]["content"]

    def test_continuity_guidance_is_task_scoped_and_does_not_change_delivery(self):
        task = make_task(id='task_history_2', last_summary='上次发现 5 条')
        messages = ScheduledTaskAgent(MagicMock(), task)._build_light_context()
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        assert '下载/任务历史/task_history_2/' in system
        assert '不完整数据覆盖有效基线' in system
        assert '首次运行没有基线' in system
        assert '不能承诺未配置的条件通知' in system
        assert 'agent_reach' in system
        assert '上次发现 5 条' in messages[-1]['content']

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
        assert "'下载/" in user_msg

    def test_with_last_summary(self):
        task = make_task(last_summary="昨日总销售额 12.5 万，比前日增长 8%")
        agent = ScheduledTaskAgent(MagicMock(), task)
        messages = agent._build_light_context()

        user_msg = messages[-1]["content"]
        assert "上次执行摘要" in user_msg
        assert "昨日总销售额 12.5 万" in user_msg

    def test_includes_confirmed_plan_and_scheduler_delivery(self):
        task = make_task(
            push_target={"type": "web", "user_id": "user_zhangsan"},
            plan_snapshot={
                "candidate": {
                    "steps": [
                        {"intent": "查询销售数据", "tools": ["erp_agent"], "required": True},
                        {"intent": "查找 webhook", "tools": ["file_search"], "required": False},
                    ],
                },
            },
        )
        agent = ScheduledTaskAgent(MagicMock(), task)
        messages = agent._build_light_context()

        system_msg = messages[0]["content"]
        user_msg = messages[-1]["content"]
        assert "调度器按任务配置投递" in system_msg
        assert "不要调用发送消息" in system_msg
        assert "已确认执行路径" in user_msg
        assert "查询销售数据" in user_msg
        assert "目标类型：web" in user_msg



# ════════════════════════════════════════════════════════
# 3. _generate_summary 摘要生成
# ════════════════════════════════════════════════════════

class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_short_text_returned_as_is(self):
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
        text = "短文本，不需要摘要"
        adapter = MagicMock()
        summary = await agent._generate_summary(text, adapter)
        assert summary == text

    @pytest.mark.asyncio
    async def test_long_text_calls_llm(self):
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
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
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
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
            files=[{"url": "https://x.com/a.xlsx", "name": "a.xlsx", "mime_type": "x", "size": 100}],
        )
        assert result.tokens_used == 1500
        assert len(result.files) == 1


class TestExecutionOutcome:
    @pytest.mark.asyncio
    async def test_legacy_task_without_execution_policy_requires_revalidation(self):
        task = make_task()
        task.pop("execution_policy")

        result = await ScheduledTaskAgent(MagicMock(), task).execute()

        assert result.status == "error"
        assert result.error_message == "scheduled_task_revalidation_required"

    @pytest.mark.asyncio
    async def test_missing_final_synthesis_is_error_and_preserves_tool_failure(self):
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
        adapter = MagicMock()
        adapter.close = AsyncMock()
        loop = MagicMock()
        loop.run = AsyncMock(return_value=LoopResult(
            text="定时任务执行未能生成完整结论，请检查任务指令。",
            total_tokens=42,
            turns=2,
            is_llm_synthesis=False,
            stop_reason="wrap_up_failure",
            failure_message="❌ 沙盒内核启动失败,请稍后重试",
        ))

        with patch.object(agent, "_prepare_template", new_callable=AsyncMock), \
             patch.object(agent, "_build_tool_loop", return_value=(loop, MagicMock())), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("core.config.get_settings", return_value=Settings(_env_file=None, database_url="postgresql://test", jwt_secret_key="test",
                 agent_loop_model="qwen3.8-max", file_workspace_root="/tmp",
                 file_workspace_enabled=True, sandbox_enabled=True, crawler_enabled=False,
                 chat_image_max_credits=100, chat_image_transparent_enabled=False,
             )), \
             patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"):
            result = await agent.execute()

        assert result.status == "error"
        assert result.error_message == "❌ 沙盒内核启动失败,请稍后重试"
        assert result.tokens_used == 42
        adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wrap_up_text_after_failure_is_not_a_successful_delivery(self):
        """模型能写出失败解释，不代表定时任务已完成。"""
        agent = ScheduledTaskAgent(IdentityDB(make_task()["user_id"], make_task()["org_id"]), make_task())
        adapter = MagicMock()
        adapter.close = AsyncMock()
        loop = MagicMock()
        loop.run = AsyncMock(return_value=LoopResult(
            text="查询超时，我缩小范围后继续查询。",
            total_tokens=99,
            turns=3,
            is_llm_synthesis=True,
            stop_reason="wrap_up_failure",
            failure_message="erp_agent:timeout",
        ))

        with patch.object(agent, "_prepare_template", new_callable=AsyncMock), \
             patch.object(agent, "_build_tool_loop", return_value=(loop, MagicMock())), \
             patch("services.adapters.factory.create_chat_adapter", return_value=adapter), \
             patch("core.config.get_settings", return_value=Settings(_env_file=None, database_url="postgresql://test", jwt_secret_key="test",
                 agent_loop_model="qwen3.8-max", file_workspace_root="/tmp",
                 file_workspace_enabled=True, sandbox_enabled=True, crawler_enabled=False,
                 chat_image_max_credits=100, chat_image_transparent_enabled=False,
             )), \
             patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"):
            result = await agent.execute()

        assert result.status == "error"
        assert result.error_message == "erp_agent:timeout"
        assert result.completion_gate["passed"] is False
        assert "loop_stopped:wrap_up_failure" in result.completion_gate["reasons"]

class TestScheduledHistoryAuthorization:
    def test_history_and_template_are_readable_but_other_tasks_are_not(self):
        task = make_task(template_file={"path": "模板/report.xlsx"})
        agent = ScheduledTaskAgent(MagicMock(), task)
        boundary = agent._file_boundary()
        assert boundary.permits("list", "下载/任务历史/task_001", browse=True)
        assert boundary.permits("read", "下载/任务历史/task_001/history.csv")
        assert boundary.permits("read", "模板/report.xlsx")
        assert not boundary.permits("read", "下载/任务历史/task_002/history.csv")
        assert not boundary.permits("read", "下载/private.csv")
        assert not boundary.permits("delete", "下载/任务历史/task_001/history.csv")
        assert not boundary.permits("restore", "下载/任务历史/task_001/history.csv")

    @pytest.mark.parametrize("task_id", ["../other", "/other", "a/b", ".", ""])
    def test_invalid_task_identity_cannot_expand_history_boundary(self, task_id):
        with pytest.raises(ValueError):
            ScheduledTaskAgent(MagicMock(), make_task(id=task_id))._file_boundary()

    @pytest.mark.asyncio
    async def test_first_run_directory_is_empty_and_existing_history_survives(self, tmp_path):
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        with patch('core.config.get_settings', return_value=SimpleNamespace(file_workspace_root=str(tmp_path))):
            await agent._prepare_history_directory()
            from services.file_executor import FileExecutor
            files = FileExecutor(str(tmp_path), agent.user_id, agent.org_id)
            directory = files.resolve_safe_path(agent._history_directory())
            assert directory.is_dir()
            history = directory / 'history.csv'
            history.write_text('baseline', encoding='utf-8')
            await agent._prepare_history_directory()
            assert history.read_text(encoding='utf-8') == 'baseline'

    @pytest.mark.asyncio
    async def test_history_file_calls_use_existing_guard_and_scope(self, tmp_path):
        from services.file_executor import FileExecutor
        from services.tools.file_calls import resolve_file_call
        from services.tools.resource_access import ResourceAccessError
        agent = ScheduledTaskAgent(MagicMock(), make_task())
        with patch('core.config.get_settings', return_value=SimpleNamespace(file_workspace_root=str(tmp_path))):
            await agent._prepare_history_directory()
            files = FileExecutor(str(tmp_path), agent.user_id, agent.org_id)
            path = agent._history_directory() + '/history.csv'
            files.resolve_safe_path(path).write_text('baseline', encoding='utf-8')
            owner = SimpleNamespace(user_id=agent.user_id, org_id=agent.org_id,
                workspace_user_id=agent.user_id, context_scope='user', conversation_id='test',
                execution_mode='scheduled', resource_manifest=None,
                resource_access_boundary=agent._file_boundary())
            search = resolve_file_call(owner, 'file_search',
                {'path': agent._history_directory(), 'scope': 'workspace'}, files=files)
            await search.prepare()
            assert search.versions
            read = resolve_file_call(owner, 'file_analyze',
                {'path': path, 'scope': 'workspace'}, files=files)
            assert read.targets[0].path == files.resolve_safe_path(path)
            with pytest.raises(ResourceAccessError, match='RESOURCE_ACTION_DENIED'):
                resolve_file_call(owner, 'file_analyze',
                    {'path': '下载/任务历史/other/history.csv', 'scope': 'workspace'}, files=files)
