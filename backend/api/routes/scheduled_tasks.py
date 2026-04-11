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
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, OrgCtx, ScopedDB, Database
from services.permissions.checker import check_permission
from services.permissions.scope_filter import apply_data_scope
from services.scheduler.cron_utils import calc_next_run, parse_cron_readable, validate_cron


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


class CreateScheduledTaskRequest(BaseModel):
    name: str = Field(..., max_length=100)
    prompt: str = Field(..., max_length=5000)
    cron_expr: str = Field(..., max_length=50)
    timezone: str = Field(default="Asia/Shanghai", max_length=50)
    push_target: Dict[str, Any]
    template_file: Optional[Dict[str, Any]] = None
    max_credits: int = Field(default=10, ge=1, le=1000)
    retry_count: int = Field(default=1, ge=0, le=5)
    timeout_sec: int = Field(default=180, ge=10, le=600)


class UpdateScheduledTaskRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    push_target: Optional[Dict[str, Any]] = None
    template_file: Optional[Dict[str, Any]] = None
    max_credits: Optional[int] = None
    retry_count: Optional[int] = None
    timeout_sec: Optional[int] = None


class ParseNLRequest(BaseModel):
    text: str = Field(..., max_length=500)


# ════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════

def _require_org(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(status_code=403, detail="此功能仅企业用户可用，请先选择企业")
    return org_ctx.org_id


def _format_task(row: Dict[str, Any]) -> Dict[str, Any]:
    """格式化任务对象（加 cron_readable）"""
    if not row:
        return row
    if row.get("cron_expr"):
        row["cron_readable"] = parse_cron_readable(row["cron_expr"])
    return row


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

    # 2. 校验 cron 表达式
    if not validate_cron(payload.cron_expr):
        raise HTTPException(400, f"cron 表达式无效: {payload.cron_expr}")

    # 3. 计算下次执行时间
    try:
        next_run = calc_next_run(payload.cron_expr, payload.timezone)
    except Exception as e:
        raise HTTPException(400, f"计算下次执行时间失败: {e}")

    # 4. 创建记录（OrgScopedDB 自动注入 org_id）
    task_id = str(uuid4())
    row = {
        "id": task_id,
        "org_id": org_id,
        "user_id": user_id,
        "name": payload.name,
        "prompt": payload.prompt,
        "cron_expr": payload.cron_expr,
        "timezone": payload.timezone,
        "push_target": payload.push_target,
        "template_file": payload.template_file,
        "status": "active",
        "max_credits": payload.max_credits,
        "retry_count": payload.retry_count,
        "timeout_sec": payload.timeout_sec,
        "next_run_at": next_run.isoformat(),
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
        update["push_target"] = payload.push_target
    if payload.template_file is not None:
        update["template_file"] = payload.template_file
    if payload.cron_expr is not None:
        if not validate_cron(payload.cron_expr):
            raise HTTPException(400, "cron 表达式无效")
        update["cron_expr"] = payload.cron_expr
        # 更新 next_run_at
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
    """V1 简化版：返回 prompt 原文 + 默认 cron，前端补全表单

    V2: 用 LLM 解析自然语言为结构化字段
    """
    org_id = _require_org(org_ctx)
    if not await check_permission(db, user_id, org_id, "task.create"):
        raise HTTPException(403, "无权创建定时任务")

    text = payload.text.strip()

    # 简单关键词推断 cron
    cron_expr = "0 9 * * *"  # 默认每天 9 点
    name = "新建任务"

    if "每周" in text or "周一" in text:
        cron_expr = "0 9 * * 1"
        name = "周报推送"
    elif "每月" in text or "1日" in text or "1号" in text:
        cron_expr = "0 9 1 * *"
        name = "月报推送"
    elif "日报" in text:
        name = "每日报表"
    elif "预警" in text or "警报" in text:
        name = "数据预警"

    return {
        "success": True,
        "data": {
            "name": name,
            "prompt": text,
            "cron_expr": cron_expr,
            "cron_readable": parse_cron_readable(cron_expr),
            "suggested_target": None,
        },
    }
