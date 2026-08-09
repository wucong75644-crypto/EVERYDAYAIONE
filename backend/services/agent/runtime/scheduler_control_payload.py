"""Normalization shared by Runtime Scheduler control implementations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
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
    if not isinstance(value, str) or not value:
        raise SchedulerCasError("SCHEDULER_RUN_AT_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchedulerCasError("SCHEDULER_RUN_AT_INVALID") from exc
    if parsed.tzinfo is None:
        raise SchedulerCasError("SCHEDULER_RUN_AT_TIMEZONE_REQUIRED")
    if parsed.astimezone(timezone.utc) < datetime.now(timezone.utc):
        raise SchedulerCasError("SCHEDULER_RUN_AT_PAST")
    return parsed


__all__ = ["normalize_scheduler_control_payload"]
