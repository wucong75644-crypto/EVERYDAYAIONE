"""跨进程周期任务数据库租约。"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)


@dataclass(frozen=True)
class PeriodicJobClaim:
    """周期任务领取结果。"""

    outcome: str
    lease_token: str | None = None


def _worker_client(db_source: Any, job_name: str) -> ScopedDatabaseClient:
    return ScopedDatabaseClient(
        db_source,
        DatabaseScope(
            actor_user_id=None,
            org_id=None,
            access_kind=DatabaseAccessKind.WORKER,
            request_id=f"periodic:{job_name}",
        ),
    )


async def _execute_rpc(db: Any, name: str, params: dict[str, Any]) -> Any:
    response = await asyncio.to_thread(
        lambda: db.rpc(name, params).execute(),
    )
    if inspect.isawaitable(response):
        response = await response
    return response.data if response is not None else None


async def claim_periodic_job(
    db_source: Any,
    job_name: str,
) -> PeriodicJobClaim:
    """领取当前周期；数据库决定周期边界和租约。"""
    payload = await _execute_rpc(
        _worker_client(db_source, job_name),
        "worker_claim_periodic_job",
        {"p_job_name": job_name},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("PERIODIC_JOB_CLAIM_INVALID")
    outcome = payload.get("outcome")
    token = payload.get("lease_token")
    if outcome == "claimed" and not token:
        raise RuntimeError("PERIODIC_JOB_CLAIM_TOKEN_MISSING")
    if outcome not in {"claimed", "busy", "completed"}:
        raise RuntimeError("PERIODIC_JOB_CLAIM_OUTCOME_INVALID")
    return PeriodicJobClaim(
        outcome=str(outcome),
        lease_token=str(token) if token else None,
    )


async def finish_periodic_job(
    db_source: Any,
    job_name: str,
    lease_token: str,
    *,
    succeeded: bool,
) -> None:
    """以当前租约 token 提交周期任务结果。"""
    payload = await _execute_rpc(
        _worker_client(db_source, job_name),
        "worker_finish_periodic_job",
        {
            "p_job_name": job_name,
            "p_lease_token": lease_token,
            "p_succeeded": succeeded,
        },
    )
    if not isinstance(payload, dict) or payload.get("outcome") != "finished":
        raise RuntimeError("PERIODIC_JOB_FINISH_INVALID")


async def renew_periodic_job(
    db_source: Any,
    job_name: str,
    lease_token: str,
) -> None:
    """续期仍由当前 token 持有的周期租约。"""
    payload = await _execute_rpc(
        _worker_client(db_source, job_name),
        "worker_renew_periodic_job",
        {
            "p_job_name": job_name,
            "p_lease_token": lease_token,
        },
    )
    if not isinstance(payload, dict) or payload.get("outcome") != "renewed":
        raise RuntimeError("PERIODIC_JOB_RENEW_INVALID")
