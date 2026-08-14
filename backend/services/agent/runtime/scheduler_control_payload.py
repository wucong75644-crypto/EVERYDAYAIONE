"""Normalization shared by Runtime Scheduler control implementations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.agent.runtime.scheduler_cas import SchedulerCasError
from services.scheduler.cron_utils import calc_next_run, compose_cron, validate_cron


def normalize_scheduler_control_payload(
    operation: str, payload: Mapping[str, object],
) -> dict[str, object]:
    if operation in {"pause", "resume", "delete"}:
        return {}
    result = dict(payload)
    _validate_fields(result)
    schedule_fields = {
        "schedule_type", "cron_expr", "run_at", "time_str",
        "weekdays", "day_of_month", "timezone",
    }
    if operation == "update" and schedule_fields.intersection(result) and "schedule_type" not in result:
        raise SchedulerCasError("SCHEDULER_SCHEDULE_TYPE_REQUIRED")
    if operation == "create" or schedule_fields.intersection(result):
        schedule_type = str(result.get("schedule_type", "cron")).lower()
        timezone_name = str(result.get("timezone", "Asia/Shanghai"))
        result["timezone"] = timezone_name
        if schedule_type == "once":
            run_at = _parse_future_run_at(result.get("run_at"))
            result.update({
                "schedule_type": "once", "cron_expr": None,
                "run_at": run_at.isoformat(),
                "next_run_at": run_at.astimezone(timezone.utc).isoformat(),
                "weekdays": None, "day_of_month": None,
            })
        else:
            cron = _resolved_cron(schedule_type, result)
            if not validate_cron(cron):
                raise SchedulerCasError("SCHEDULER_CRON_INVALID")
            result.update({
                "schedule_type": schedule_type, "cron_expr": cron,
                "run_at": None,
                "next_run_at": calc_next_run(cron, timezone_name).isoformat(),
            })
    result.pop("time_str", None)
    return result


def scheduler_resume_next_run(
    schedule: Mapping[str, object], *, base: datetime | None = None,
) -> str:
    """Calculate resume time from a server-authoritative schedule snapshot."""
    schedule_type = str(schedule.get("schedule_type", ""))
    timezone_name = str(schedule.get("timezone", ""))
    _validate_fields({"timezone": timezone_name})
    now = base or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SchedulerCasError("SCHEDULER_RESUME_BASE_TIMEZONE_REQUIRED")
    if schedule_type == "once":
        run_at = _parse_datetime(schedule.get("run_at"))
        if run_at.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
            raise SchedulerCasError("SCHEDULER_ONCE_RESUME_EXPIRED")
        return run_at.astimezone(timezone.utc).isoformat()
    if schedule_type not in {"daily", "weekly", "monthly", "cron"}:
        raise SchedulerCasError("SCHEDULER_SCHEDULE_TYPE_INVALID")
    cron_expr = schedule.get("cron_expr")
    if not isinstance(cron_expr, str) or not validate_cron(cron_expr):
        raise SchedulerCasError("SCHEDULER_CRON_INVALID")
    return calc_next_run(cron_expr, timezone_name, base=now).isoformat()


async def scheduler_resume_parameters(
    rpc: Callable[[str, Mapping[str, object]], Awaitable[Mapping[str, object]]],
    attempt: object, task_id: str, expected_version: int,
    attempt_state_version: int, dispatch_intent_id: str, *, enabled: bool,
) -> tuple[str | None, str | None]:
    if not enabled:
        return None, None
    scope = attempt.scope
    context = await rpc("get_agent_runtime_scheduled_task_resume_context_v1", {
        "p_attempt_id": str(attempt.attempt_id),
        "p_action_id": str(attempt.action_id), "p_run_id": str(attempt.run_id),
        "p_org_id": str(scope.org_id), "p_user_id": str(scope.user_id),
        "p_scope_kind": scope.kind.value, "p_scope_id": str(scope.scope_id),
        "p_task_id": task_id, "p_expected_state_version": expected_version,
        "p_attempt_state_version": attempt_state_version,
        "p_request_hash": str(attempt.request_hash),
        "p_execution_token": str(attempt.lease.fencing_token),
        "p_dispatch_intent_id": dispatch_intent_id,
    })
    if context.get("calculation_revision") != (
        "services.scheduler.cron_utils.calc_next_run:v1"
    ):
        raise SchedulerCasError("SCHEDULER_RESUME_CALCULATION_REVISION_INVALID")
    schedule_hash = context.get("schedule_hash")
    if not isinstance(schedule_hash, str) or not schedule_hash:
        raise SchedulerCasError("SCHEDULER_RESUME_CONTEXT_INVALID")
    return schedule_hash, scheduler_resume_next_run(context)


def _validate_fields(payload: Mapping[str, object]) -> None:
    _bounded_text(payload, "name", 100)
    _bounded_text(payload, "prompt", 5000)
    _bounded_text(payload, "timezone", 50)
    if "timezone" in payload:
        try:
            ZoneInfo(str(payload["timezone"]))
        except ZoneInfoNotFoundError as exc:
            raise SchedulerCasError("SCHEDULER_TIMEZONE_INVALID") from exc
    _bounded_int(payload, "max_credits", 1, 1000)
    _bounded_int(payload, "retry_count", 0, 5)
    _bounded_int(payload, "timeout_sec", 10, 600)
    if "push_target" in payload and not isinstance(payload["push_target"], Mapping):
        raise SchedulerCasError("SCHEDULER_PUSH_TARGET_INVALID")


def _bounded_text(payload: Mapping[str, object], key: str, maximum: int) -> None:
    if key not in payload:
        return
    value = payload[key]
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SchedulerCasError(f"SCHEDULER_{key.upper()}_INVALID")


def _bounded_int(
    payload: Mapping[str, object], key: str, minimum: int, maximum: int,
) -> None:
    if key not in payload:
        return
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SchedulerCasError(f"SCHEDULER_{key.upper()}_INVALID")


def _resolved_cron(schedule_type: str, payload: Mapping[str, object]) -> str:
    if schedule_type == "cron":
        cron = payload.get("cron_expr")
        if not isinstance(cron, str) or not cron:
            raise SchedulerCasError("SCHEDULER_CRON_REQUIRED")
        return cron
    if schedule_type not in {"daily", "weekly", "monthly"}:
        raise SchedulerCasError("SCHEDULER_SCHEDULE_TYPE_INVALID")
    try:
        cron = compose_cron(
            schedule_type, str(payload.get("time_str", "")),
            payload.get("weekdays"), payload.get("day_of_month"),
        )
    except (TypeError, ValueError) as exc:
        raise SchedulerCasError("SCHEDULER_SCHEDULE_INVALID") from exc
    if not cron:
        raise SchedulerCasError("SCHEDULER_SCHEDULE_INVALID")
    return cron


def _parse_future_run_at(value: object) -> datetime:
    parsed = _parse_datetime(value)
    if parsed.astimezone(timezone.utc) < datetime.now(timezone.utc):
        raise SchedulerCasError("SCHEDULER_RUN_AT_PAST")
    return parsed


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise SchedulerCasError("SCHEDULER_RUN_AT_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchedulerCasError("SCHEDULER_RUN_AT_INVALID") from exc
    if parsed.tzinfo is None:
        raise SchedulerCasError("SCHEDULER_RUN_AT_TIMEZONE_REQUIRED")
    return parsed


__all__ = [
    "normalize_scheduler_control_payload", "scheduler_resume_next_run",
    "scheduler_resume_parameters",
]
