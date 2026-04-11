"""定时任务 REST API

设计文档: docs/document/TECH_定时任务心跳系统.md §7
权限集成: docs/document/TECH_定时任务心跳系统.md §5

路由：
- POST   /scheduled-tasks                    创建
- GET    /scheduled-tasks                    列表（自动数据范围过滤）
- GET    /scheduled-tasks/{id}               详情
- PATCH  /scheduled-tasks/{id}               修改
- DELETE /scheduled-tasks/{id}               删除
- POST   /scheduled-tasks/{id}/run           立即执行
- POST   /scheduled-tasks/{id}/pause         暂停
- POST   /scheduled-tasks/{id}/resume        恢复
- GET    /scheduled-tasks/{id}/runs          执行历史
- GET    /scheduled-tasks/chat-targets       可用推送目标列表
- POST   /scheduled-tasks/parse              自然语言解析
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, ScopedDB, Database
from services.permissions.checker import check_permission
from services.permissions.scope_filter import apply_data_scope
from services.scheduler.cron_utils import (
    calc_next_run,
    compose_cron,
    parse_cron_readable,
    validate_cron,
)


router = APIRouter(prefix="/scheduled-tasks", tags=["定时任务"])


# ════════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════════

class PushTarget(BaseModel):
    type: Literal["wecom_group", "wecom_user", "web", "multi"]
    chatid: Optional[str] = None
    chat_name: Optional[str] = None
    wecom_userid: Optional[str] = None
    name: Optional[str] = None
    user_id: Optional[str] = None
    targets: Optional[List[Dict[str, Any]]] = None


class TemplateFile(BaseModel):
    path: str
    name: str
    url: Optional[str] = None


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

    # 频率结构化字段（V2）
    schedule_type: ScheduleType = "cron"
    # cron 类型：直接传 cron_expr
    cron_expr: Optional[str] = Field(default=None, max_length=50)
    # daily/weekly/monthly：传 time_str + (weekdays | day_of_month)
    time_str: Optional[str] = Field(default=None, max_length=5)  # "HH:MM"
    weekdays: Optional[List[int]] = None  # [0=日, 1=一, ..., 6=六]
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    # once：传 run_at（ISO 8601 含时区）
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

    # 频率结构化字段（V2，可选——只有传了 schedule_type 才走重新组装逻辑）
    schedule_type: Optional[ScheduleType] = None
    cron_expr: Optional[str] = None
    time_str: Optional[str] = None
    weekdays: Optional[List[int]] = None
    day_of_month: Optional[int] = None
    run_at: Optional[str] = None


class ParseNLRequest(BaseModel):
    text: str = Field(..., max_length=500)


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _require_org(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(status_code=403, detail="此功能仅企业用户可用，请先选择企业")
    return org_ctx.org_id


def _is_push_to_self(db: Any, current_user_id: str, push_target: Dict[str, Any]) -> bool:
    """
    判断 push_target 是否指向当前用户自己（无需 task.push_to_others 权限）。

    判定规则：
    - type == "web" 且 user_id == current_user_id  → 自己
    - type == "wecom_user" 且 wecom_userid 在当前用户的 wecom_user_mappings 中 → 自己
    - 其他（wecom_group / 别人的 wecom_user / multi）→ 不是自己
    """
    if not isinstance(push_target, dict):
        return False

    ptype = push_target.get("type")
    if ptype == "web":
        return push_target.get("user_id") == current_user_id

    if ptype == "wecom_user":
        target_wecom_userid = push_target.get("wecom_userid")
        if not target_wecom_userid:
            return False
        try:
            result = (
                db.table("wecom_user_mappings")
                .select("wecom_userid")
                .eq("user_id", current_user_id)
                .eq("wecom_userid", target_wecom_userid)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.warning(f"_is_push_to_self lookup failed | error={e}")
            return False

    return False


def _format_task(row: Dict[str, Any]) -> Dict[str, Any]:
    """格式化任务对象（加 cron_readable）"""
    if not row:
        return row
    if row.get("cron_expr"):
        row["cron_readable"] = parse_cron_readable(row["cron_expr"])
    return row


def _resolve_schedule_fields(payload: Any, tz: str) -> Dict[str, Any]:
    """
    把 payload 里的频率结构化字段（schedule_type / time_str / weekdays /
    day_of_month / run_at / cron_expr）解析成 DB 写入字段。

    Returns:
        {
            "schedule_type": str,
            "cron_expr": Optional[str],
            "weekdays": Optional[List[int]],
            "day_of_month": Optional[int],
            "run_at": Optional[str],     # ISO timestamp
            "next_run_at": str,          # ISO timestamp，必有
        }

    Raises:
        HTTPException 400: 参数缺失或非法
    """
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
            run_at_dt = datetime.fromisoformat(payload.run_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, f"run_at 格式无效: {payload.run_at}")
        if run_at_dt.tzinfo is None:
            from zoneinfo import ZoneInfo
            run_at_dt = run_at_dt.replace(tzinfo=ZoneInfo(tz))
        # 不允许过去时间（留 60 秒余量给客户端时钟漂移）
        now_utc = datetime.now(timezone.utc)
        if run_at_dt.astimezone(timezone.utc) < now_utc - timedelta(seconds=60):
            raise HTTPException(400, "执行时间不能早于当前时间")
        result["run_at"] = run_at_dt.isoformat()
        result["next_run_at"] = run_at_dt.astimezone(timezone.utc).isoformat()
        return result

    if schedule_type == "cron":
        if not payload.cron_expr:
            raise HTTPException(400, "cron 类型必须指定 cron_expr")
        if not validate_cron(payload.cron_expr):
            raise HTTPException(400, f"cron 表达式无效: {payload.cron_expr}")
        result["cron_expr"] = payload.cron_expr
    else:
        # daily / weekly / monthly → 组装 cron
        try:
            cron = compose_cron(
                schedule_type=schedule_type,
                time_str=payload.time_str or "",
                weekdays=payload.weekdays,
                day_of_month=payload.day_of_month,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not cron:
            raise HTTPException(400, f"{schedule_type} 类型无法组装 cron")
        result["cron_expr"] = cron
        if schedule_type == "weekly":
            result["weekdays"] = sorted({int(d) for d in (payload.weekdays or [])})
        if schedule_type == "monthly":
            result["day_of_month"] = payload.day_of_month

    # 计算 next_run_at
    try:
        next_run = calc_next_run(result["cron_expr"], tz)
    except Exception as e:
        raise HTTPException(400, f"计算下次执行时间失败: {e}")
    result["next_run_at"] = next_run.isoformat()
    return result


async def _enrich_with_creator(db: Any, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量补充创建者展示信息（用于老板/主管视角）"""
    if not tasks:
        return tasks

    user_ids = list({t["user_id"] for t in tasks if t.get("user_id")})
    if not user_ids:
        return tasks

    # 1. 查 users 基本信息
    users_resp = db.table("users") \
        .select("id, nickname, avatar_url") \
        .in_("id", user_ids) \
        .execute()
    users_map = {u["id"]: u for u in (users_resp.data or [])}

    # 2. 查 assignments + departments + positions
    assignments_resp = db.table("org_member_assignments") \
        .select("user_id, department_id, position_id") \
        .in_("user_id", user_ids) \
        .eq("is_primary", True) \
        .execute()
    assignments_map = {a["user_id"]: a for a in (assignments_resp.data or [])}

    dept_ids = [a["department_id"] for a in (assignments_resp.data or []) if a.get("department_id")]
    pos_ids = [a["position_id"] for a in (assignments_resp.data or []) if a.get("position_id")]

    dept_map: Dict[str, Dict[str, Any]] = {}
    if dept_ids:
        depts_resp = db.table("org_departments") \
            .select("id, name, type") \
            .in_("id", dept_ids) \
            .execute()
        dept_map = {d["id"]: d for d in (depts_resp.data or [])}

    pos_map: Dict[str, Dict[str, Any]] = {}
    if pos_ids:
        pos_resp = db.table("org_positions") \
            .select("id, code") \
            .in_("id", pos_ids) \
            .execute()
        pos_map = {p["id"]: p for p in (pos_resp.data or [])}

    # 3. 拼装 creator
    for task in tasks:
        uid = task.get("user_id")
        if not uid:
            continue
        user = users_map.get(uid, {})
        assignment = assignments_map.get(uid, {})
        dept = dept_map.get(assignment.get("department_id"), {}) if assignment else {}
        pos = pos_map.get(assignment.get("position_id"), {}) if assignment else {}

        task["creator"] = {
            "name": user.get("nickname", "未知"),
            "avatar": user.get("avatar_url"),
            "department_id": dept.get("id"),
            "department_name": dept.get("name"),
            "department_type": dept.get("type"),
            "position_code": pos.get("code"),
        }
    return tasks


# ════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════

@router.post("", summary="创建定时任务")
async def create_task(
    payload: CreateScheduledTaskRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    # 1. 权限校验
    if not await check_permission(db, user_id, org_id, "task.create"):
        raise HTTPException(403, "无权创建定时任务")

    # 1.5 推送目标权限校验：推送给他人/群聊需要 task.push_to_others
    if not _is_push_to_self(db, user_id, payload.push_target):
        if not await check_permission(db, user_id, org_id, "task.push_to_others"):
            raise HTTPException(
                403, "无权将定时任务推送给同事或群聊（需要管理职位）"
            )

    # 2. 解析频率字段（once / daily / weekly / monthly / cron）
    schedule = _resolve_schedule_fields(payload, payload.timezone)

    # 3. 创建记录（OrgScopedDB 自动注入 org_id）
    task_id = str(uuid4())
    row = {
        "id": task_id,
        "org_id": org_id,
        "user_id": user_id,
        "name": payload.name,
        "prompt": payload.prompt,
        "cron_expr": schedule["cron_expr"],
        "schedule_type": schedule["schedule_type"],
        "weekdays": schedule["weekdays"],
        "day_of_month": schedule["day_of_month"],
        "run_at": schedule["run_at"],
        "timezone": payload.timezone,
        "push_target": payload.push_target,
        "template_file": payload.template_file,
        "status": "active",
        "max_credits": payload.max_credits,
        "retry_count": payload.retry_count,
        "timeout_sec": payload.timeout_sec,
        "next_run_at": schedule["next_run_at"],
        "run_count": 0,
        "consecutive_failures": 0,
    }
    scoped_db.table("scheduled_tasks").insert(row).execute()

    return {"success": True, "data": _format_task(row)}


@router.get("", summary="列出定时任务")
async def list_tasks(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
    view: str = Query("default", description="default=按权限自动过滤 | mine=只看自己 | dept=按部门"),
    dept_id: Optional[str] = None,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    # 权限：所有人都可创建查看（数据范围由 apply_data_scope 决定）
    if not await check_permission(db, user_id, org_id, "task.view"):
        raise HTTPException(403, "无权查看定时任务")

    query = scoped_db.table("scheduled_tasks").select("*")

    if view == "mine":
        query = query.eq("user_id", user_id)
    elif view == "dept" and dept_id:
        # 主管/副总切换到指定部门视图
        from services.permissions.scope_filter import get_users_in_depts
        dept_user_ids = await get_users_in_depts(db, [dept_id])
        if dept_user_ids:
            query = query.in_("user_id", list(dept_user_ids))
        else:
            query = query.eq("user_id", user_id)
    else:
        # 默认按权限自动注入
        query = await apply_data_scope(db, query, user_id, org_id, "task.view")

    result = query.order("next_run_at", desc=False).execute()
    tasks = list(result.data or [])
    tasks = await _enrich_with_creator(db, tasks)
    tasks = [_format_task(t) for t in tasks]

    return {"success": True, "data": tasks, "total": len(tasks)}


@router.get("/chat-targets", summary="获取可用推送目标")
async def list_chat_targets(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
) -> Dict[str, Any]:
    """从 wecom_chat_targets 表查询可用的群和单聊"""
    org_id = _require_org(org_ctx)

    try:
        result = scoped_db.table("wecom_chat_targets") \
            .select("chatid, chat_type, chat_name, last_active") \
            .eq("is_active", True) \
            .order("last_active", desc=True) \
            .execute()
        targets = list(result.data or [])
    except Exception as e:
        logger.error(f"list_chat_targets failed: {e}")
        targets = []

    return {"success": True, "data": targets}


@router.get("/{task_id}", summary="任务详情")
async def get_task(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.view", task):
        raise HTTPException(403, "无权查看此任务")

    enriched = await _enrich_with_creator(db, [task])
    return {"success": True, "data": _format_task(enriched[0])}


@router.patch("/{task_id}", summary="修改任务")
async def update_task(
    task_id: str,
    payload: UpdateScheduledTaskRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    # 查任务
    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.edit", task):
        raise HTTPException(403, "无权编辑此任务")

    update: Dict[str, Any] = {}
    for field in ("name", "prompt", "timezone", "max_credits", "retry_count", "timeout_sec"):
        val = getattr(payload, field, None)
        if val is not None:
            update[field] = val
    if payload.push_target is not None:
        # 改推送目标也要校验权限：改成给他人/群聊需要 task.push_to_others
        if not _is_push_to_self(db, user_id, payload.push_target):
            if not await check_permission(db, user_id, org_id, "task.push_to_others"):
                raise HTTPException(
                    403, "无权将定时任务推送给同事或群聊（需要管理职位）"
                )
        update["push_target"] = payload.push_target
    if payload.template_file is not None:
        update["template_file"] = payload.template_file

    # 频率字段：只有传 schedule_type 时才走重新组装；
    # 否则只兼容老接口（仅传 cron_expr）
    if payload.schedule_type is not None:
        tz = payload.timezone or task.get("timezone", "Asia/Shanghai")
        schedule = _resolve_schedule_fields(payload, tz)
        update["schedule_type"] = schedule["schedule_type"]
        update["cron_expr"] = schedule["cron_expr"]
        update["weekdays"] = schedule["weekdays"]
        update["day_of_month"] = schedule["day_of_month"]
        update["run_at"] = schedule["run_at"]
        update["next_run_at"] = schedule["next_run_at"]
    elif payload.cron_expr is not None:
        # 老接口：仅传 cron_expr 时维持向后兼容
        if not validate_cron(payload.cron_expr):
            raise HTTPException(400, "cron 表达式无效")
        update["cron_expr"] = payload.cron_expr
        update["next_run_at"] = calc_next_run(
            payload.cron_expr, payload.timezone or task.get("timezone", "Asia/Shanghai")
        ).isoformat()

    if update:
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        scoped_db.table("scheduled_tasks").update(update).eq("id", task_id).execute()

    return {"success": True}


@router.delete("/{task_id}", summary="删除任务")
async def delete_task(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.delete", task):
        raise HTTPException(403, "无权删除此任务")

    scoped_db.table("scheduled_tasks").delete().eq("id", task_id).execute()
    return {"success": True}


@router.post("/{task_id}/pause", summary="暂停任务")
async def pause_task(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.edit", task):
        raise HTTPException(403, "无权暂停此任务")

    scoped_db.table("scheduled_tasks").update({
        "status": "paused",
        "next_run_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", task_id).execute()
    return {"success": True}


@router.post("/{task_id}/resume", summary="恢复任务")
async def resume_task(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.edit", task):
        raise HTTPException(403, "无权恢复此任务")

    next_run = calc_next_run(task["cron_expr"], task.get("timezone", "Asia/Shanghai"))
    scoped_db.table("scheduled_tasks").update({
        "status": "active",
        "next_run_at": next_run.isoformat(),
        "consecutive_failures": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", task_id).execute()
    return {"success": True}


@router.post("/{task_id}/run", summary="立即执行任务")
async def run_task_now(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    """立即触发任务执行（异步，不等待结果）"""
    org_id = _require_org(org_ctx)

    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.execute", task):
        raise HTTPException(403, "无权立即执行此任务")

    # 异步执行（不阻塞 HTTP 响应）
    import asyncio
    from services.scheduler.task_executor import ScheduledTaskExecutor
    executor = ScheduledTaskExecutor(db)
    asyncio.create_task(executor.execute(dict(task)))

    return {"success": True, "message": "任务已开始执行，请稍后查看执行历史"}


@router.get("/{task_id}/runs", summary="执行历史")
async def list_runs(
    task_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)

    # 先校验任务可见性
    result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(404, "任务不存在")
    task = result.data[0]

    if not await check_permission(db, user_id, org_id, "task.view", task):
        raise HTTPException(403, "无权查看此任务的执行历史")

    runs = scoped_db.table("scheduled_task_runs") \
        .select("*") \
        .eq("task_id", task_id) \
        .order("started_at", desc=True) \
        .limit(limit) \
        .execute()

    return {"success": True, "data": list(runs.data or [])}


@router.post("/parse", summary="自然语言解析为结构化任务")
async def parse_nl_task(
    payload: ParseNLRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """LLM 解析自然语言为结构化任务字段

    返回的字段直接对应 CreateScheduledTaskRequest:
    - name / prompt / schedule_type / time_str / weekdays / day_of_month / run_at

    LLM 不可用时降级到关键词兜底，永远返回可用结果。
    """
    org_id = _require_org(org_ctx)
    if not await check_permission(db, user_id, org_id, "task.create"):
        raise HTTPException(403, "无权创建定时任务")

    from services.scheduler.task_nl_parser import parse_task_nl
    parsed = await parse_task_nl(payload.text, tz="Asia/Shanghai")

    # 计算 cron_readable 用于 UI 展示（only for daily/weekly/monthly）
    cron_readable: Optional[str] = None
    schedule_type = parsed.get("schedule_type")
    if schedule_type in ("daily", "weekly", "monthly"):
        try:
            cron = compose_cron(
                schedule_type=schedule_type,
                time_str=parsed.get("time_str") or "09:00",
                weekdays=parsed.get("weekdays"),
                day_of_month=parsed.get("day_of_month"),
            )
            if cron:
                cron_readable = parse_cron_readable(cron)
        except Exception:
            pass

    return {
        "success": True,
        "data": {
            **parsed,
            "cron_readable": cron_readable,
            "suggested_target": None,
        },
    }
