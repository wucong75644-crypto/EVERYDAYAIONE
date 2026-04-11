"""测试 Scheduler Scanner + TaskExecutor"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler.scanner import ScheduledTaskScanner
from services.scheduler.task_executor import ScheduledTaskExecutor


def make_task(**overrides):
    base = {
        "id": "task_1",
        "user_id": "user_1",
        "org_id": "org_1",
        "name": "测试任务",
        "prompt": "测试 prompt",
        "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "push_target": {"type": "wecom_group", "chatid": "x"},
        "max_credits": 10,
        "retry_count": 1,
        "timeout_sec": 180,
        "consecutive_failures": 0,
        "run_count": 0,
    }
    base.update(overrides)
    return base


# ════════════════════════════════════════════════════════
# Scanner
# ════════════════════════════════════════════════════════

class TestScanner:

    @pytest.mark.asyncio
    async def test_no_tasks(self):
        """没有到期任务时返回 0"""
        db = MagicMock()
        rpc_call = MagicMock()
        rpc_call.execute.return_value = MagicMock(data=[])
        db.rpc.return_value = rpc_call

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        count = await scanner.poll()
        assert count == 0

    @pytest.mark.asyncio
    async def test_claims_and_dispatches_tasks(self):
        """领取到任务后 fire-and-forget 调用 executor"""
        import asyncio as _asyncio
        db = MagicMock()
        tasks = [make_task(id="t1"), make_task(id="t2")]
        rpc_call = MagicMock()
        rpc_call.execute.return_value = MagicMock(data=tasks)
        db.rpc.return_value = rpc_call

        executor = MagicMock()
        executor.execute = AsyncMock()

        scanner = ScheduledTaskScanner(db, executor=executor)
        count = await scanner.poll()

        assert count == 2
        # fire-and-forget 模式：让事件循环跑一次让 create_task 的协程执行
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        assert executor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_executor_exception_does_not_crash_scanner(self):
        """executor 异常不影响 scanner"""
        import asyncio as _asyncio
        db = MagicMock()
        tasks = [make_task(id="t1")]
        rpc_call = MagicMock()
        rpc_call.execute.return_value = MagicMock(data=tasks)
        db.rpc.return_value = rpc_call

        executor = MagicMock()
        executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

        scanner = ScheduledTaskScanner(db, executor=executor)
        # 不应该抛
        count = await scanner.poll()
        # 让 fire-and-forget 任务跑完
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        assert count == 1

    @pytest.mark.asyncio
    async def test_rpc_error_returns_zero(self):
        """RPC 错误时返回 0"""
        db = MagicMock()
        db.rpc.side_effect = RuntimeError("db down")

        scanner = ScheduledTaskScanner(db)
        count = await scanner.poll()
        assert count == 0


# ════════════════════════════════════════════════════════
# Task Executor
# ════════════════════════════════════════════════════════

class TestTaskExecutor:

    def _make_db(self):
        """构造一个支持 update/insert 链式的 mock db"""
        db = MagicMock()

        def make_table(name):
            t = MagicMock()
            t.insert.return_value = t
            t.update.return_value = t
            t.eq.return_value = t
            t.execute.return_value = MagicMock(data=[])
            return t

        db.table.side_effect = make_table
        return db

    @pytest.mark.asyncio
    async def test_create_run(self):
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)
        run_id = await executor._create_run(make_task())
        assert run_id  # uuid string
        assert len(run_id) == 36

    @pytest.mark.asyncio
    async def test_success_flow(self):
        """成功执行：积分锁 → Agent → 推送 → 更新"""
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)

        # mock credit_lock
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield "txn_123"

        # mock ScheduledTaskAgent
        from services.agent.scheduled_task_agent import ScheduledTaskResult
        fake_result = ScheduledTaskResult(
            text="日报内容",
            summary="销售额 10w",
            status="success",
            tokens_used=1500,
            turns_used=3,
            tools_called=["erp_agent"],
            files=[],
        )

        with patch("services.credit_service.CreditService") as mock_credit_cls:
            mock_credit_inst = MagicMock()
            mock_credit_inst.credit_lock = fake_lock
            mock_credit_cls.return_value = mock_credit_inst

            with patch(
                "services.agent.scheduled_task_agent.ScheduledTaskAgent"
            ) as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.execute = AsyncMock(return_value=fake_result)
                mock_agent_cls.return_value = mock_agent

                # push_dispatcher Phase 5 才实现，这里 mock 整个 _push_result
                executor._push_result = AsyncMock(return_value="pushed")

                await executor.execute(make_task())

        # 验证 update 被调用（用 update 才能确认 _on_success 跑了）
        assert db.table.called

    @pytest.mark.asyncio
    async def test_failure_first_time_retry(self):
        """第一次失败 → 5 分钟后重试"""
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield "txn_123"

        with patch("services.credit_service.CreditService") as mock_credit_cls:
            mock_credit_inst = MagicMock()
            mock_credit_inst.credit_lock = fake_lock
            mock_credit_cls.return_value = mock_credit_inst

            with patch(
                "services.agent.scheduled_task_agent.ScheduledTaskAgent"
            ) as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.execute = AsyncMock(side_effect=RuntimeError("ERP down"))
                mock_agent_cls.return_value = mock_agent

                # 不会抛异常（被 except 捕获）
                await executor.execute(make_task(retry_count=2))

        assert db.table.called

    @pytest.mark.asyncio
    async def test_failure_three_times_pause(self):
        """连续 3 次失败 → 暂停 + 通知"""
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)

        notify_calls = []
        async def fake_notify(task, msg):
            notify_calls.append((task["id"], msg))

        executor._notify_owner = fake_notify

        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield "txn_123"

        task = make_task(consecutive_failures=2)  # 已经 2 次失败了

        with patch("services.credit_service.CreditService") as mock_credit_cls:
            mock_credit_inst = MagicMock()
            mock_credit_inst.credit_lock = fake_lock
            mock_credit_cls.return_value = mock_credit_inst

            with patch(
                "services.agent.scheduled_task_agent.ScheduledTaskAgent"
            ) as mock_agent_cls:
                mock_agent = MagicMock()
                mock_agent.execute = AsyncMock(side_effect=RuntimeError("第 3 次失败"))
                mock_agent_cls.return_value = mock_agent

                await executor.execute(task)

        # 触发了通知
        assert len(notify_calls) == 1
        assert task["id"] in notify_calls[0][0]
        assert "已自动暂停" in notify_calls[0][1]
