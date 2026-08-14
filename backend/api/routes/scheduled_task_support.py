"""Scheduled-task request models and route presentation helpers."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from core.db_scope import PostgresArray
from services.scheduler.cron_utils import (
    calc_next_run,
    compose_cron,
    parse_cron_readable,
    validate_cron,
)


ScheduleType = Literal["once", "daily", "weekly", "monthly", "cron"]


class CreateScheduledTaskRequest(BaseModel):
    name: str = Field(..., max_length=100)
    prompt: str = Field(..., max_length=5000)
    timezone: str = Field(default="Asia/Shanghai", max_length=50)
    push_target: Dict[str, Any]
    template_file: Optional[Dict[str, Any]] = None
    max_credits: int = Field(default=10, ge=1, le=1000)
    retry_count: int = Field(default=1, ge=0, le=5)
    timeout_sec: int = Field(default=180, ge=10, le=600)
    schedule_type: ScheduleType = "cron"
    cron_expr: Optional[str] = Field(default=None, max_length=50)
    time_str: Optional[str] = Field(default=None, max_length=5)
    weekdays: Optional[List[int]] = None
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    run_at: Optional[str] = Field(default=None, max_length=64)


class UpdateScheduledTaskRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    timezone: Optional[str] = None
    push_target: Optional[Dict[str, Any]] = None
    template_file: Optional[Dict[str, Any]] = None
    max_credits: Optional[int] = None
    retry_count: Optional[int] = None
    timeout_sec: Optional[int] = None
    schedule_type: Optional[ScheduleType] = None
    cron_expr: Optional[str] = None
    time_str: Optional[str] = None
    weekdays: Optional[List[int]] = None
    day_of_month: Optional[int] = None
    run_at: Optional[str] = None


class ParseNLRequest(BaseModel):
    text: str = Field(..., max_length=500)


def resolve_schedule_fields(payload: Any, tz: str) -> Dict[str, Any]:
    schedule_type = (payload.schedule_type or "cron").lower()
    result: Dict[str, Any] = {
        "schedule_type": schedule_type,
        "cron_expr": None,
        "weekdays": None,
        "day_of_month": None,
        "run_at": None,
    }
    if schedule_type == "once":
        if not payload.run_at:
            raise HTTPException(400, "单次任务必须指定 run_at（ISO 8601 时间）")
        try:
            run_at = datetime.fromisoformat(payload.run_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(400, f"run_at 格式无效: {payload.run_at}") from exc
        if run_at.tzinfo is None:
            from zoneinfo import ZoneInfo
            run_at = run_at.replace(tzinfo=ZoneInfo(tz))
        if run_at.astimezone(timezone.utc) < (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ):
            raise HTTPException(400, "执行时间不能早于当前时间")
        result["run_at"] = run_at.isoformat()
        result["next_run_at"] = run_at.astimezone(timezone.utc).isoformat()
        return result
    if schedule_type == "cron":
        if not payload.cron_expr:
            raise HTTPException(400, "cron 类型必须指定 cron_expr")
        if not validate_cron(payload.cron_expr):
            raise HTTPException(400, f"cron 表达式无效: {payload.cron_expr}")
        result["cron_expr"] = payload.cron_expr
    else:
        try:
            cron = compose_cron(
                schedule_type, payload.time_str or "",
                payload.weekdays, payload.day_of_month,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not cron:
            raise HTTPException(400, f"{schedule_type} 类型无法组装 cron")
        result["cron_expr"] = cron
        if schedule_type == "weekly":
            result["weekdays"] = sorted(set(payload.weekdays or []))
        if schedule_type == "monthly":
            result["day_of_month"] = payload.day_of_month
    try:
        result["next_run_at"] = calc_next_run(result["cron_expr"], tz).isoformat()
    except Exception as exc:
        raise HTTPException(400, f"计算下次执行时间失败: {exc}") from exc
    return result


async def enrich_with_creator(
    db: Any,
    tasks: List[Dict[str, Any]],
    org_id: str,
) -> List[Dict[str, Any]]:
    user_ids = list({task["user_id"] for task in tasks if task.get("user_id")})
    if not user_ids:
        return tasks
    users = db.table("users").select("id, nickname, avatar_url") \
        .in_("id", user_ids).execute()
    users_map = {row["id"]: row for row in (users.data or [])}
    assignments = db.rpc("list_runtime_member_assignments", {
        "p_org_id": org_id,
        "p_user_ids": PostgresArray(user_ids),
    }).execute()
    assignment_map = {
        row["user_id"]: row for row in (assignments.data or [])
    }
    for task in tasks:
        user = users_map.get(task.get("user_id"), {})
        assignment = assignment_map.get(task.get("user_id"), {})
        task["creator"] = {
            "name": user.get("nickname", "未知"),
            "avatar": user.get("avatar_url"),
            "department_id": assignment.get("department_id"),
            "department_name": assignment.get("department_name"),
            "department_type": assignment.get("department_type"),
            "position_code": assignment.get("position_code"),
        }
    return tasks


def request_runtime_scheduled_execution(
    scoped_db: Any,
    task: Dict[str, Any],
    task_id: str,
    org_id: str,
    user_id: str,
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    """Read back or submit a Runtime-owned scheduled task.

    Scheduled execution must never fall back to the legacy scheduler owner.
    """
    if not task.get("runtime_action_id"):
        raise HTTPException(
            503,
            "该定时任务尚未接入 Runtime，已阻止旧执行链路",
        )
    stable_key = (idempotency_key or "").strip()
    if not 1 <= len(stable_key) <= 128:
        raise HTTPException(400, "Runtime 定时任务需要有效的 Idempotency-Key")
    result = scoped_db.rpc(
        "request_agent_runtime_scheduled_execution_v1",
        {
            "p_request_id": stable_key,
            "p_task_id": task_id,
            "p_org_id": org_id,
            "p_user_id": user_id,
            "p_expected_task_version": int(task.get("runtime_state_version", 0)),
            "p_now": datetime.now(timezone.utc).isoformat(),
        },
    ).execute()
    data = result.data if isinstance(result.data, dict) else {}
    if data.get("owner_kind") != "runtime":
        raise HTTPException(503, "无法确认 Runtime 定时任务执行 Owner")
    if data.get("outcome") not in {"submitted", "already_submitted"}:
        raise HTTPException(503, "Runtime 定时执行尚未开放")
    return {
        "success": True,
        "message": "任务已提交 Runtime，请稍后查看执行历史",
        "command_id": data.get("command_id"),
    }
