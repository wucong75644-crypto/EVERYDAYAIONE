"""定时任务结果必须先持久化为 Outbox，再由企微 Worker 投递。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.scheduler.task_executor import ScheduledTaskExecutor


def _task(target=None):
    return {
        "id": "task", "org_id": "org", "user_id": "user", "name": "日报",
        "max_credits": 10, "cron_expr": "0 9 * * *",
        "schedule_type": "cron", "timezone": "Asia/Shanghai",
        "push_target": target or {"type": "wecom_group", "chatid": "chat"},
    }


def _result():
    return SimpleNamespace(
        text="今日完成", summary="摘要", tokens_used=12, turns_used=2,
        files=[{"name": "report.xlsx", "url": "https://example.test/report"}],
    )


def test_wecom_delivery_snapshot_is_deduplicated_and_immutable_from_target_shape():
    deliveries = ScheduledTaskExecutor._build_wecom_deliveries(_task({
        "type": "multi",
        "targets": [
            {"type": "wecom_group", "chatid": "chat"},
            {"type": "wecom_group", "chatid": "chat"},
            {"type": "web", "user_id": "user"},
        ],
    }), _result())

    assert len(deliveries) == 1
    assert deliveries[0]["delivery_kind"] == "result"
    assert deliveries[0]["target_context"] == {
        "type": "wecom_group", "chatid": "chat",
    }
    assert deliveries[0]["payload"]["files"][0]["name"] == "report.xlsx"


@pytest.mark.asyncio
async def test_success_uses_atomic_completion_rpc_instead_of_push_dispatcher():
    db = MagicMock()
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = SimpleNamespace(data=[{
        "cron_expr": "0 9 * * *", "schedule_type": "cron",
        "timezone": "Asia/Shanghai",
    }])
    db.table.return_value = query
    db.rpc.return_value.execute.return_value = SimpleNamespace(data={
        "outcome": "completed", "push_status": "queued",
    })
    executor = ScheduledTaskExecutor(db)
    executor._push_ws_event = AsyncMock()

    status = await executor._on_success(
        _task(), "run", _result(), datetime.now(timezone.utc), 3,
    )

    assert status == "queued"
    name, params = db.rpc.call_args.args
    assert name == "complete_scheduled_task_success"
    assert params["p_deliveries"][0]["target_context"]["chatid"] == "chat"
    assert params["p_deliveries"][0]["payload"]["text"] == "今日完成"


@pytest.mark.asyncio
async def test_manual_run_of_paused_task_stays_paused_when_it_fails():
    db = MagicMock()
    query = MagicMock()
    query.update.return_value = query
    query.eq.return_value = query
    query.execute.return_value = SimpleNamespace(data=[])
    db.table.return_value = query
    executor = ScheduledTaskExecutor(db)
    executor._push_ws_event = AsyncMock()
    task = _task()
    task.update({
        "_manual_run": True,
        "_previous_status": "paused",
        "consecutive_failures": 0,
        "retry_count": 1,
    })

    await executor._on_failure(
        task, "run", RuntimeError("failed"), None, datetime.now(timezone.utc),
    )

    task_update = query.update.call_args_list[-1].args[0]
    assert task_update["status"] == "paused"
    assert task_update["next_run_at"] is None
