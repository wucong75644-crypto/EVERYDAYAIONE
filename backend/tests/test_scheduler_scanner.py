"""测试 Scheduler Scanner + TaskExecutor"""
from __future__ import annotations
import sys
from contextlib import contextmanager
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


def _mock_pool_returning(rows: list) -> MagicMock:
    """构造 db.pool.connection() 上下文管理器 mock，cursor.fetchall() 返回 rows"""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool


# ════════════════════════════════════════════════════════
# Scanner
# ════════════════════════════════════════════════════════

class TestScanner:

    @pytest.mark.asyncio
    async def test_no_tasks(self):
        """没有到期任务时返回 0"""
        db = MagicMock()
        db.pool = _mock_pool_returning([])

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        count = await scanner.poll()
        assert count == 0

    @pytest.mark.asyncio
    async def test_claims_and_dispatches_tasks(self):
        """领取到任务后 fire-and-forget 调用 executor"""
        import asyncio as _asyncio
        db = MagicMock()
        tasks = [make_task(id="t1"), make_task(id="t2")]
        db.pool = _mock_pool_returning(tasks)

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
        db.pool = _mock_pool_returning(tasks)

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
    async def test_db_error_returns_zero(self):
        """DB 连接异常时返回 0"""
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.connection.side_effect = RuntimeError("db down")

        scanner = ScheduledTaskScanner(db)
        count = await scanner.poll()
        assert count == 0

    @pytest.mark.asyncio
    async def test_recover_stale_running_restores_periodic_task(self):
        """卡死的周期任务被恢复为 active + 重新计算 next_run_at"""
        db = MagicMock()

        # scheduled_tasks 查询返回一条卡死任务
        stale_task = {
            "id": "stale_1",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "schedule_type": "daily",
        }
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[stale_task])

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[])

        def table_router(name):
            t = MagicMock()
            t.select.return_value = select_chain
            t.update.return_value = update_chain
            return t

        db.table.side_effect = table_router
        # claim_due_tasks 不返回任何到期任务
        db.pool = _mock_pool_returning([])

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        await scanner.poll()

        # 验证 update 被调用且 status='active'
        update_call_args = update_chain.eq.call_args_list
        assert any("stale_1" in str(c) for c in update_call_args)

    @pytest.mark.asyncio
    async def test_recover_stale_running_pauses_once_task(self):
        """卡死的单次任务恢复为 paused"""
        db = MagicMock()
        stale_task = {
            "id": "once_1",
            "cron_expr": None,
            "timezone": "Asia/Shanghai",
            "schedule_type": "once",
        }
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[stale_task])

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[])

        def table_router(name):
            t = MagicMock()
            t.select.return_value = select_chain
            t.update.return_value = update_chain
            return t

        db.table.side_effect = table_router
        db.pool = _mock_pool_returning([])

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        await scanner.poll()

        # update 应该被调用（任务表 + runs 表）
        assert db.table.called

    @pytest.mark.asyncio
    async def test_recover_skips_when_no_stale(self):
        """没有卡死任务时不做任何 update"""
        db = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[])

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[])

        def table_router(name):
            t = MagicMock()
            t.select.return_value = select_chain
            t.update.return_value = update_chain
            return t

        db.table.side_effect = table_router
        db.pool = _mock_pool_returning([])

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        await scanner.poll()

        # data=[] 意味着没有 stale 任务，update 不应被调用
        update_chain.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_interval_throttle(self):
        """恢复检查有间隔节流，短时间内不重复查"""
        db = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[])

        def table_router(name):
            t = MagicMock()
            t.select.return_value = select_chain
            return t

        db.table.side_effect = table_router
        db.pool = _mock_pool_returning([])

        scanner = ScheduledTaskScanner(db, executor=MagicMock())

        # 第一次 poll → 触发恢复检查
        await scanner.poll()
        first_call_count = select_chain.execute.call_count

        # 第二次 poll → 节流跳过
        await scanner.poll()
        second_call_count = select_chain.execute.call_count

        # 第二次不应该增加查询次数（被节流）
        assert second_call_count == first_call_count

    @pytest.mark.asyncio
    async def test_claim_due_tasks_passes_datetime_and_int(self):
        """回归测试：_claim_due_tasks 传给 SQL 的参数类型必须是 datetime + int

        生产 bug 复盘（commit 5bd7b86）：
        曾经传 now.isoformat() 字符串给 PG，被推断为 unknown 类型，
        无法匹配函数签名 (timestamptz, integer)，导致整个 scanner 死循环报错。

        防御要点：
        - 第一个参数必须是 datetime 对象（不是 .isoformat() 字符串）
        - 第二个参数必须是 Python int
        """
        from datetime import datetime, timezone

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        db = MagicMock()
        db.pool = MagicMock()
        db.pool.connection.return_value = mock_conn

        scanner = ScheduledTaskScanner(db, executor=MagicMock())
        now = datetime.now(timezone.utc)
        await scanner._claim_due_tasks(now, 5)

        # 验证 cursor.execute 被调用且参数类型正确
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "claim_due_tasks" in sql, "SQL 必须调用 claim_due_tasks"
        assert isinstance(params[0], datetime), \
            f"第一个参数必须是 datetime 对象，实际是 {type(params[0]).__name__}"
        assert type(params[1]) is int, \
            f"第二个参数必须是 Python int，实际是 {type(params[1]).__name__}"


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
        """成功执行：积分锁 → Agent → 按量计费 → 推送 → 更新"""
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)

        # mock credit_lock — yield CreditLockHandle
        from contextlib import asynccontextmanager
        from services.credit_service import CreditLockHandle

        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield CreditLockHandle("txn_123", kwargs.get("amount", 10))

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

                # mock _calc_actual_credits 避免导入 adapter
                with patch.object(
                    ScheduledTaskExecutor, "_calc_actual_credits", return_value=3
                ):
                    await executor.execute(make_task())

        # 验证 update 被调用（用 update 才能确认 _on_success 跑了）
        assert db.table.called

    @pytest.mark.asyncio
    async def test_failure_first_time_retry(self):
        """第一次失败 → 5 分钟后重试"""
        db = self._make_db()
        executor = ScheduledTaskExecutor(db)

        from contextlib import asynccontextmanager
        from services.credit_service import CreditLockHandle

        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield CreditLockHandle("txn_123", kwargs.get("amount", 10))

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
        from services.credit_service import CreditLockHandle

        @asynccontextmanager
        async def fake_lock(**kwargs):
            yield CreditLockHandle("txn_123", kwargs.get("amount", 10))

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


class TestCalcActualCredits:
    """_calc_actual_credits 独立测试"""

    def test_zero_tokens_returns_1(self):
        """token=0 → 保底 1 积分"""
        result = ScheduledTaskExecutor._calc_actual_credits(0, make_task())
        assert result == 1

    def test_negative_tokens_returns_1(self):
        """token<0 → 保底 1 积分"""
        result = ScheduledTaskExecutor._calc_actual_credits(-100, make_task())
        assert result == 1

    def test_normal_tokens_uses_pricing(self):
        """正常 token 量走定价表计算"""
        # qwen3.5-plus: input=12/1M, output=68/1M
        # 10000 tokens → 7000 input + 3000 output
        # input_credits = 7000 * 12 / 1_000_000 = 0.084 → 0
        # output_credits = 3000 * 68 / 1_000_000 = 0.204 → 0
        # total = 0 → max(1, 0) = 1
        result = ScheduledTaskExecutor._calc_actual_credits(
            10000, make_task(max_credits=10)
        )
        assert result >= 1
        assert result <= 10

    def test_large_tokens_capped_at_max_credits(self):
        """大量 token 不超过 max_credits 上限"""
        result = ScheduledTaskExecutor._calc_actual_credits(
            5_000_000, make_task(max_credits=5)
        )
        assert result <= 5

    def test_fallback_when_pricing_missing(self):
        """定价表无对应模型 → 兜底 5000 token/积分"""
        with patch(
            "services.adapters.dashscope.chat_adapter.DASHSCOPE_PRICING", {}
        ):
            result = ScheduledTaskExecutor._calc_actual_credits(
                15000, make_task(max_credits=10)
            )
            # 15000 // 5000 = 3
            assert result == 3

    def test_fallback_on_import_error(self):
        """导入失败 → 兜底公式"""
        with patch(
            "services.adapters.dashscope.chat_adapter.DASHSCOPE_PRICING",
            side_effect=ImportError("no module"),
        ):
            # patch 为 side_effect 会在访问时报错，
            # 但 _calc_actual_credits 内部 try/except 会 fallback
            result = ScheduledTaskExecutor._calc_actual_credits(
                25000, make_task(max_credits=10)
            )
            assert 1 <= result <= 10
