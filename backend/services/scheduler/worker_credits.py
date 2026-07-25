"""定时任务 Worker 的任务范围积分上下文。"""

from contextlib import asynccontextmanager
from typing import Any

from services.credit_service import CreditLockHandle
from services.scheduler.worker_store import ScheduledRunLease


class ScheduledWorkerCredits:
    def __init__(self, db: Any) -> None:
        self._db = db

    @asynccontextmanager
    async def lock(self, task_id: str, run: ScheduledRunLease):
        locked = self._db.rpc(
            "worker_lock_scheduled_credits",
            {
                "p_task_id": task_id,
                "p_run_id": run.run_id,
                "p_execution_token": run.execution_token,
            },
        ).execute()
        data = locked.data if isinstance(locked.data, dict) else {}
        if data.get("outcome") != "locked":
            raise RuntimeError(
                f"SCHEDULED_CREDIT_LOCK_FAILED:{data.get('outcome', 'invalid')}"
            )
        handle = CreditLockHandle(
            str(data["transaction_id"]),
            int(data["locked_amount"]),
        )
        try:
            yield handle
        except Exception:
            self._settle(task_id, run, handle, success=False)
            raise
        final_used = self._settle(task_id, run, handle, success=True)
        handle._refund_succeeded = final_used == handle.actual_amount

    def _settle(
        self,
        task_id: str,
        run: ScheduledRunLease,
        handle: CreditLockHandle,
        *,
        success: bool,
    ) -> int:
        result = self._db.rpc(
            "worker_settle_scheduled_credits",
            {
                "p_task_id": task_id,
                "p_run_id": run.run_id,
                "p_execution_token": run.execution_token,
                "p_transaction_id": handle.transaction_id,
                "p_success": success,
                "p_actual_amount": handle.actual_amount if success else None,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        if data.get("outcome") not in {
            "confirmed", "refunded", "already_settled"
        }:
            raise RuntimeError("SCHEDULED_CREDIT_SETTLE_FAILED")
        return int(data.get("final_credits_used", handle.locked_amount))
