"""ScopedDB adapter for the Runtime task-cancel v2 facade."""

from __future__ import annotations

from typing import Any, Mapping

from services.agent.runtime.task_cancel import (
    RuntimeTaskCancelConflict,
    RuntimeTaskCancelOutcome,
    RuntimeTaskCancelReceipt,
    RuntimeTaskCancelRequest,
    RuntimeTaskCancelUnavailable,
)


class PostgresRuntimeTaskCancelRepository:
    """Call only the public hash-free cancel facade and parse fail-closed."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def cancel(
        self, request: RuntimeTaskCancelRequest,
    ) -> RuntimeTaskCancelReceipt:
        try:
            response = self._database.rpc(
                "request_agent_runtime_task_cancel_v2", {
                    "p_task_id": request.task_id,
                    "p_message_id": request.message_id,
                    "p_org_id": request.org_id,
                    "p_user_id": request.user_id,
                    "p_session_id": request.session_id,
                    "p_submit_command_id": request.submit_command_id,
                    "p_idempotency_key": request.idempotency_key,
                },
            ).execute()
        except Exception as exc:
            if self._sqlstate(exc) in {"22023", "42501"}:
                raise RuntimeTaskCancelConflict(
                    "RUNTIME_TASK_CANCEL_ASSOCIATION_REJECTED",
                ) from None
            raise RuntimeTaskCancelUnavailable(
                "RUNTIME_TASK_CANCEL_DATABASE_UNAVAILABLE",
            ) from None
        data = response.data if response is not None else None
        if not isinstance(data, Mapping) or not isinstance(
            data.get("outcome"), str,
        ):
            raise RuntimeTaskCancelUnavailable(
                "RUNTIME_TASK_CANCEL_RECEIPT_INVALID",
            )
        try:
            outcome = RuntimeTaskCancelOutcome(data["outcome"])
        except ValueError:
            raise RuntimeTaskCancelUnavailable(
                "RUNTIME_TASK_CANCEL_RECEIPT_INVALID",
            ) from None
        return RuntimeTaskCancelReceipt(outcome=outcome)

    @staticmethod
    def _sqlstate(exc: Exception) -> str | None:
        for candidate in (exc, exc.__cause__, exc.__context__):
            state = getattr(candidate, "sqlstate", None)
            if isinstance(state, str):
                return state
        return None
