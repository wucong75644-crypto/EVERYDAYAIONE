"""定时任务 REST API

设计文档: docs/document/TECH_定时任务心跳系统.md §7
权限集成: docs/document/TECH_定时任务心跳系统.md §5

路由：
- POST   /scheduled-tasks                    创建
- POST   /scheduled-tasks/changesets         创建受控 ChangeSet
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
from types import SimpleNamespace
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
from services.scheduler.scheduled_task_change_adapter import (
    ScheduledTaskChangeError,
    ScheduledTaskChangeSetService,
    task_snapshot,
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


class ConfirmScheduledTaskDraftRequest(BaseModel):
    config_hash: str = Field(..., min_length=64, max_length=64)


class ScheduledTaskChangeRequest(BaseModel):
    operation: Literal["create", "update", "pause", "resume", "delete"]
    task_id: Optional[str] = None
    definition: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)
    # 聊天入口用这些定位键把 ChangeSet ID 回写为消息中的展示引用；
    # 非聊天 API 调用可以不传，不能以消息内容代替 ChangeSet 状态。
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    form_id: Optional[str] = None


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


def _draft_definition(payload: CreateScheduledTaskRequest, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """草稿与预检的不可变输入；启用时由 RPC 原样消费。"""
    return {
        "name": payload.name, "prompt": payload.prompt, "timezone": payload.timezone,
        "push_target": payload.push_target, "template_file": payload.template_file,
        "max_credits": payload.max_credits, "retry_count": payload.retry_count,
        "timeout_sec": payload.timeout_sec, **schedule,
    }


def _revision_definition(
    task: Dict[str, Any],
    payload: UpdateScheduledTaskRequest,
    schedule: Dict[str, Any],
) -> Dict[str, Any]:
    """把现有任务与修订输入合成为新的、待预检的不可变定义。"""
    return {
        "name": payload.name if payload.name is not None else task["name"],
        "prompt": payload.prompt if payload.prompt is not None else task["prompt"],
        "timezone": payload.timezone if payload.timezone is not None else task.get("timezone", "Asia/Shanghai"),
        "push_target": payload.push_target if payload.push_target is not None else task.get("push_target") or {},
        "template_file": payload.template_file if payload.template_file is not None else task.get("template_file"),
        "max_credits": payload.max_credits if payload.max_credits is not None else task.get("max_credits", 10),
        "retry_count": payload.retry_count if payload.retry_count is not None else task.get("retry_count", 1),
        "timeout_sec": payload.timeout_sec if payload.timeout_sec is not None else task.get("timeout_sec", 180),
        **schedule,
    }


async def _propose_task_change(
    *,
    db: Any,
    scoped_db: Any,
    user_id: str,
    org_id: str,
    operation: str,
    task_id: str | None = None,
    definition: Dict[str, Any] | None = None,
    base_task: Dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    """新入口先创建 ChangeSet，再由后台完成 Planner 与只读试跑。"""
    task = base_task
    if operation != "create" and not task_id:
        raise HTTPException(422, "该操作必须提供 task_id")
    if operation != "create" and task is None:
        result = scoped_db.table("scheduled_tasks").select("*").eq("id", task_id).execute()
        if not result.data:
            raise HTTPException(404, "任务不存在")
        task = result.data[0]
    if operation != "create" and not task_id:
        task_id = str(task["id"])
    proposed = dict(definition or {})
    if operation in {"pause", "resume", "delete"}:
        proposed = task_snapshot(task)
        if operation == "pause":
            proposed.update({"status": "paused", "next_run_at": None})
        elif operation == "resume":
            if task.get("schedule_type") == "once":
                raw = task.get("run_at")
                if not raw:
                    raise HTTPException(409, "一次性任务缺少执行时间，不能恢复")
                run_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if run_at.tzinfo is None:
                    raise HTTPException(409, "一次性任务执行时间必须包含时区")
                next_run = max(run_at, datetime.now(timezone.utc))
            else:
                next_run = calc_next_run(task["cron_expr"], task.get("timezone", "Asia/Shanghai"))
            proposed.update({"status": "active", "next_run_at": next_run.isoformat()})
    if operation == "create":
        proposed.setdefault("status", "active")
    service = ScheduledTaskChangeSetService(db, user_id=user_id, org_id=org_id)
    try:
        return await service.begin(
            operation=operation,
            resource_id=None if operation == "create" else task_id,
            base_snapshot=task,
            proposed_snapshot=proposed, idempotency_key=idempotency_key,
        )
    except ScheduledTaskChangeError as exc:
        raise HTTPException(exc.status_code, {"message": str(exc), "reasons": exc.details}) from exc


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
    raise HTTPException(
        409,
        "请使用 POST /scheduled-tasks/changesets 完成规划与安全试跑后确认创建",
    )


@router.post("/changesets", summary="创建定时任务 ChangeSet")
async def propose_task_changeset(
    payload: ScheduledTaskChangeRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    """新入口：返回可供第三批前端展示和确认的 ChangeSet DTO。"""
    org_id = _require_org(org_ctx)
    references = (payload.message_id, payload.conversation_id, payload.form_id)
    if any(references) and not all(references):
        raise HTTPException(422, "聊天表单引用参数不完整")
    row = await _propose_task_change(
        db=db, scoped_db=scoped_db, user_id=user_id, org_id=org_id,
        operation=payload.operation, task_id=payload.task_id,
        definition=payload.definition, idempotency_key=payload.idempotency_key,
    )
    if all(references):
        try:
            from services.conversation_service import ConversationService

            await ConversationService(db).get_conversation(
                payload.conversation_id, user_id, org_id,
            )
            response = db.rpc("attach_chat_form_changeset", {
                "p_message_id": payload.message_id,
                "p_conversation_id": payload.conversation_id,
                "p_org_id": org_id,
                "p_form_id": payload.form_id,
                "p_change_set_id": str(row["id"]),
                "p_result_message": "正在生成变更方案，完成后请在卡片中确认。",
            }).execute()
            attached = response.data if response else None
            outcome = attached.get("outcome") if isinstance(attached, dict) else None
            if outcome not in {"transitioned", "existing"}:
                messages = {
                    "message_missing": "聊天消息不存在，方案仍可在 ChangeSet 中查看",
                    "form_missing": "聊天表单不存在，方案仍可在 ChangeSet 中查看",
                    "state_conflict": "聊天表单状态已变化，请刷新后查看",
                    "changeset_missing": "变更方案不存在，请重新规划",
                }
                raise HTTPException(409, messages.get(outcome, "聊天表单引用同步失败，请重试"))
        except HTTPException:
            raise
        except Exception as exc:
            # ChangeSet 已经按幂等键落库；返回可重试的安全错误，下一次同键请求
            # 会回放同一个 ChangeSet 并再次完成消息引用绑定。
            logger.warning(
                "Scheduled task ChangeSet message reference sync failed | "
                f"change_set_id={row.get('id')} | error={type(exc).__name__}"
            )
            raise HTTPException(503, "ChangeSet 已创建，但聊天状态同步失败，请重试") from exc
    from api.routes.change_sets import _to_dto
    return {"success": True, "data": _to_dto(row, row.get("checks") or [])}


@router.post("/drafts", summary="规划并试跑定时任务")
async def create_task_draft(
    payload: CreateScheduledTaskRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> Dict[str, Any]:
    """创建草稿并同步完成只读预检。该路径不创建 active 任务、不扣积分、不投递。"""
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

    from services.scheduler.scheduled_task_workflow import create_draft_and_preflight
    draft = await create_draft_and_preflight(
        db=db, org_id=org_id, user_id=user_id,
        definition=_draft_definition(payload, schedule),
    )
    return {"success": True, "data": draft}


@router.get("/drafts/{draft_id}", summary="查看定时任务预检草稿")
async def get_task_draft(
    draft_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    if not await check_permission(db, user_id, org_id, "task.create"):
        raise HTTPException(403, "无权创建定时任务")
    result = scoped_db.table("scheduled_task_drafts").select("*").eq("id", draft_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(404, "任务草稿不存在")
    draft = result.data[0]
    if draft.get("latest_preflight_id"):
        runs = scoped_db.table("scheduled_task_preflight_runs").select("*").eq("id", draft["latest_preflight_id"]).execute()
        draft["latest_preflight"] = runs.data[0] if runs.data else None
    return {"success": True, "data": draft}


@router.post("/drafts/{draft_id}/confirm", summary="确认启用已预检的定时任务")
async def confirm_task_draft(
    draft_id: str,
    payload: ConfirmScheduledTaskDraftRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
) -> Dict[str, Any]:
    org_id = _require_org(org_ctx)
    result = scoped_db.table("scheduled_task_drafts").select("*").eq("id", draft_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(404, "任务草稿不存在")
    draft = result.data[0]
    source_task_id = draft.get("source_task_id")
    if source_task_id:
        source = scoped_db.table("scheduled_tasks").select("*").eq("id", source_task_id).execute()
        if not source.data or not await check_permission(db, user_id, org_id, "task.edit", source.data[0]):
            raise HTTPException(403, "无权修改此定时任务")
    elif not await check_permission(db, user_id, org_id, "task.create"):
        raise HTTPException(403, "无权创建定时任务")
    definition = draft.get("definition") or {}
    if payload.config_hash != draft.get("config_hash"):
        raise HTTPException(409, "任务配置已变化，请重新规划并试跑")
    try:
        schedule = _resolve_schedule_fields(SimpleNamespace(**definition), definition.get("timezone") or "Asia/Shanghai")
    except HTTPException:
        raise HTTPException(409, "任务时间配置已失效，请重新规划并试跑")
    task_id = str(uuid4())
    response = db.rpc("confirm_scheduled_task_draft", {
        "p_draft_id": draft_id, "p_org_id": org_id, "p_user_id": user_id,
        "p_config_hash": payload.config_hash, "p_task_id": task_id,
        "p_next_run_at": schedule["next_run_at"],
    }).execute()
    outcome = response.data if response else None
    if not isinstance(outcome, dict) or outcome.get("outcome") not in {"created", "updated", "confirmed"}:
        if isinstance(outcome, dict) and outcome.get("outcome") == "source_running":
            raise HTTPException(409, "任务正在执行，暂不能替换配置；请稍后重新确认")
        raise HTTPException(409, "预检尚未通过、已过期或配置已变化")
    final_id = str(outcome.get("task_id") or task_id)
    task = scoped_db.table("scheduled_tasks").select("*").eq("id", final_id).execute()
    if not task.data:
        raise HTTPException(503, "任务已确认但读取失败，请刷新后检查")
    return {"success": True, "data": _format_task(task.data[0])}


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

    if payload.push_target is not None:
        # 改推送目标也要校验权限：改成给他人/群聊需要 task.push_to_others
        if not _is_push_to_self(db, user_id, payload.push_target):
            if not await check_permission(db, user_id, org_id, "task.push_to_others"):
                raise HTTPException(
                    403, "无权将定时任务推送给同事或群聊（需要管理职位）"
                )
    # 任何会影响实际执行的修改都必须重新走 AI 规划与安全试跑；原任务在
    # 用户确认草稿前保持完全不变。
    effective = SimpleNamespace(
        schedule_type=payload.schedule_type or task.get("schedule_type"),
        cron_expr=(
            payload.cron_expr
            if payload.cron_expr is not None
            else task.get("cron_expr")
        ),
        time_str=payload.time_str,
        weekdays=(payload.weekdays if payload.weekdays is not None else task.get("weekdays")),
        day_of_month=(payload.day_of_month if payload.day_of_month is not None else task.get("day_of_month")),
        run_at=(payload.run_at if payload.run_at is not None else task.get("run_at")),
    )
    # 已有 daily/weekly/monthly 任务没有 time_str，需从 cron 恢复给统一校验器。
    if effective.schedule_type in {"daily", "weekly", "monthly"} and not effective.time_str:
        cron_parts = str(effective.cron_expr or "").split()
        if len(cron_parts) >= 2:
            effective.time_str = f"{int(cron_parts[1]):02d}:{int(cron_parts[0]):02d}"
    tz = payload.timezone or task.get("timezone", "Asia/Shanghai")
    schedule = _resolve_schedule_fields(effective, tz)

    # 兼容已经打开的旧任务表单：该路径只创建旧 draft，不直接写入任务。
    # 新入口使用 POST /scheduled-tasks/changesets，统一进入 ChangeSet 适配器。
    from services.scheduler.scheduled_task_workflow import create_draft_and_preflight
    draft = await create_draft_and_preflight(
        db=db,
        org_id=org_id,
        user_id=user_id,
        definition=_revision_definition(task, payload, schedule),
        source_task_id=task_id,
    )
    return {"success": True, "data": draft}


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

    row = await _propose_task_change(
        db=db, scoped_db=scoped_db, user_id=user_id, org_id=org_id,
        operation="delete", task_id=task_id, base_task=task,
    )
    from api.routes.change_sets import _to_dto
    return {"success": True, "data": _to_dto(row, row.get("checks") or [])}


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

    row = await _propose_task_change(
        db=db, scoped_db=scoped_db, user_id=user_id, org_id=org_id,
        operation="pause", task_id=task_id, base_task=task,
    )
    from api.routes.change_sets import _to_dto
    return {"success": True, "data": _to_dto(row, row.get("checks") or [])}


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

    if task.get("schedule_type") == "once":
        raw_run_at = task.get("run_at")
        if not raw_run_at:
            raise HTTPException(409, "一次性任务缺少执行时间，不能恢复")
        try:
            run_at = datetime.fromisoformat(str(raw_run_at).replace("Z", "+00:00"))
        except ValueError as error:
            raise HTTPException(409, "一次性任务执行时间无效，不能恢复") from error
        if run_at.tzinfo is None:
            raise HTTPException(409, "一次性任务执行时间必须包含时区")
        # 明确恢复已经过期的一次性任务时，下一轮轮询立即领取一次。
        next_run = max(run_at, datetime.now(timezone.utc))
    else:
        next_run = calc_next_run(
            task["cron_expr"], task.get("timezone", "Asia/Shanghai"),
        )
    row = await _propose_task_change(
        db=db, scoped_db=scoped_db, user_id=user_id, org_id=org_id,
        operation="resume", task_id=task_id, base_task=task,
    )
    from api.routes.change_sets import _to_dto
    return {"success": True, "data": _to_dto(row, row.get("checks") or [])}


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

    # 原子领取，避免 Scanner 同时把同一任务作为到期任务执行。
    claim_response = db.rpc("claim_scheduled_task_now", {
        "p_task_id": task_id,
        "p_org_id": org_id,
    }).execute()
    claim = claim_response.data if claim_response else None
    if not isinstance(claim, dict):
        logger.error("scheduled_task_run_now_claim_invalid | task={}", task_id)
        raise HTTPException(503, "任务暂时无法领取，请稍后重试")
    if claim.get("outcome") == "already_running":
        raise HTTPException(409, "任务正在执行中")
    if claim.get("outcome") != "claimed" or not isinstance(claim.get("task"), dict):
        logger.error(
            "scheduled_task_run_now_claim_failed | task={} | outcome={}",
            task_id, claim.get("outcome"),
        )
        raise HTTPException(503, "任务暂时无法领取，请稍后重试")

    # 异步执行（不阻塞 HTTP 响应）。此前状态随任务快照传入，完成时恢复
    # paused/error 的手动任务，避免一次点击意外开启长期调度。
    import asyncio
    from services.scheduler.task_executor import ScheduledTaskExecutor
    executor = ScheduledTaskExecutor(db)
    claimed_task = dict(claim["task"])
    claimed_task["_manual_run"] = True
    claimed_task["_previous_status"] = claim.get("previous_status")
    asyncio.create_task(executor.execute(claimed_task))

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
    run_rows = list(runs.data or [])
    run_ids = [row["id"] for row in run_rows]
    if run_ids:
        events = scoped_db.table("scheduled_task_execution_events") \
            .select("execution_id,step_order,event_type,tool_name,status,elapsed_ms,summary") \
            .eq("execution_kind", "run") \
            .in_("execution_id", run_ids) \
            .order("step_order") \
            .execute()
        event_map: Dict[str, List[Dict[str, Any]]] = {}
        for event in events.data or []:
            event_map.setdefault(str(event["execution_id"]), []).append(event)
        for run in run_rows:
            run["events"] = event_map.get(str(run["id"]), [])

    return {"success": True, "data": run_rows}


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
