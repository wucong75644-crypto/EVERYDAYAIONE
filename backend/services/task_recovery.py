"""通过受限 Worker RPC 原子恢复部署重启时中断的非 Actor 任务。"""

from typing import Any

from loguru import logger

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from services.task_utils import merge_blocks_with_text


_CLAIM_LIMIT = 100
_LEASE_SECONDS = 60
_INTERRUPTED_ERROR = "服务重启，任务中断（无已生成内容）"


def _recovery_db(db: Any) -> ScopedDatabaseClient:
    return ScopedDatabaseClient(
        db,
        DatabaseScope(
            actor_user_id=None,
            org_id=None,
            access_kind=DatabaseAccessKind.WORKER,
            request_id="orphan-task-recovery",
        ),
    )


def _message_content(task: dict[str, Any]) -> list[dict[str, Any]] | None:
    accumulated = task.get("accumulated_content") or ""
    if not accumulated.strip() or not task.get("placeholder_message_id"):
        return None
    blocks = task.get("accumulated_blocks")
    if blocks:
        return merge_blocks_with_text(blocks, accumulated)
    return [{"type": "text", "text": accumulated}]


def _rpc_data(
    db: ScopedDatabaseClient,
    name: str,
    params: dict[str, Any],
) -> Any:
    response = db.rpc(name, params).execute()
    return response.data if response else None


def _settle_claimed_task(
    db: ScopedDatabaseClient,
    task: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    params = {
        "p_task_id": task.get("id"),
        "p_execution_token": task.get("execution_token"),
    }
    content = _message_content(task)
    if content is None:
        return (
            False,
            _rpc_data(
                db,
                "worker_fail_orphan_task",
                {**params, "p_error_message": _INTERRUPTED_ERROR},
            ),
        )
    return (
        True,
        _rpc_data(
            db,
            "worker_complete_orphan_task",
            {**params, "p_content": content},
        ),
    )


async def recover_orphan_tasks(db: Any) -> int:
    """Claim and settle recoverable tasks through fenced owner RPCs."""
    recovery_db = _recovery_db(db)
    recovered = 0
    while True:
        try:
            tasks = _rpc_data(
                recovery_db,
                "worker_claim_orphan_tasks",
                {
                    "p_limit": _CLAIM_LIMIT,
                    "p_lease_seconds": _LEASE_SECONDS,
                },
            )
        except Exception as exc:
            logger.error(
                "Failed to claim orphan tasks | "
                f"error_type={type(exc).__name__}"
            )
            return recovered
        if not tasks:
            return recovered

        for task in tasks:
            task_id = task.get("id")
            try:
                has_content, outcome = _settle_claimed_task(recovery_db, task)
                if has_content and outcome and outcome.get(
                    "outcome"
                ) in ("completed", "already_completed"):
                    recovered += 1
                logger.info(
                    "Orphan task recovery settled | "
                    f"task_id={task_id} | "
                    f"outcome={outcome.get('outcome') if outcome else 'empty'}"
                )
            except Exception as exc:
                logger.error(
                    "Failed to settle orphan task | "
                    f"task_id={task_id} | "
                    f"error_type={type(exc).__name__}"
                )
