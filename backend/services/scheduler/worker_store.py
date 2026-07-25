"""定时任务 Worker 的受控数据库能力。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScheduledRunLease:
    run_id: str
    execution_token: str


class ScheduledWorkerStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    def claim_due(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        result = self._db.rpc(
            "worker_claim_due_scheduled_tasks",
            {"p_now": now.isoformat(), "p_limit": limit},
        ).execute()
        return list(result.data or [])

    def list_stale(self, cutoff: datetime) -> list[dict[str, Any]]:
        result = self._db.rpc(
            "worker_list_stale_scheduled_tasks",
            {"p_cutoff": cutoff.isoformat()},
        ).execute()
        return list(result.data or [])

    def recover_stale(
        self,
        task_id: str,
        cutoff: datetime,
        status: str,
        next_run_at: datetime | None,
        now: datetime,
    ) -> bool:
        result = self._db.rpc(
            "worker_recover_stale_scheduled_task",
            {
                "p_task_id": task_id,
                "p_cutoff": cutoff.isoformat(),
                "p_status": status,
                "p_next_run_at": (
                    next_run_at.isoformat() if next_run_at else None
                ),
                "p_now": now.isoformat(),
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") == "recovered"

    def create_run(self, task_id: str) -> ScheduledRunLease | None:
        result = self._db.rpc(
            "worker_create_scheduled_run",
            {"p_task_id": task_id, "p_lease_seconds": 90},
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        run = data.get("run") if data.get("outcome") == "created" else None
        token = data.get("execution_token")
        if not isinstance(run, dict) or not run.get("id") or not token:
            return None
        return ScheduledRunLease(
            run_id=str(run["id"]),
            execution_token=str(token),
        )

    def renew(
        self,
        task_id: str,
        run: ScheduledRunLease,
        lease_seconds: int = 90,
    ) -> bool:
        result = self._db.rpc(
            "worker_renew_scheduled_run",
            {
                "p_task_id": task_id,
                "p_run_id": run.run_id,
                "p_execution_token": run.execution_token,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") == "renewed"

    def get_task(
        self,
        task_id: str,
        run: ScheduledRunLease,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_get_scheduled_task",
            {
                "p_task_id": task_id,
                "p_run_id": run.run_id,
                "p_execution_token": run.execution_token,
            },
        ).execute()
        return result.data if isinstance(result.data, dict) else None

    def append_result_message(
        self,
        task_id: str,
        run: ScheduledRunLease,
        text: str,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_append_scheduled_result_message",
            {
                "p_task_id": task_id,
                "p_run_id": run.run_id,
                "p_execution_token": run.execution_token,
                "p_text": text,
            },
        ).execute()
        return result.data if isinstance(result.data, dict) else None

    def complete_run(self, **params: Any) -> bool:
        result = self._db.rpc(
            "worker_complete_scheduled_run",
            params,
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") == "completed"

    def fail_run(self, **params: Any) -> bool:
        result = self._db.rpc(
            "worker_fail_scheduled_run",
            params,
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") == "failed"
