"""
任务管理路由

提供任务查询、恢复等接口
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from typing import Dict, Any
from pydantic import BaseModel, Field

from api.deps import CurrentUser, Database
from core.limiter import limiter


class MarkTaskFailedRequest(BaseModel):
    """标记任务失败请求"""
    reason: str = Field(
        default="用户取消或超时",
        max_length=500,
        description="失败原因"
    )

router = APIRouter(prefix="/tasks", tags=["任务管理"])


@router.get("/pending", summary="获取用户进行中任务")
@limiter.limit("30/minute")
async def get_pending_tasks(
    request: Request,
    current_user: CurrentUser,
    db: Database,
) -> Dict[str, Any]:
    """
    获取当前用户所有进行中的任务

    用于页面刷新/登录后恢复轮询。
    返回所有status为'pending'或'running'的任务。

    速率限制：每分钟最多 30 次请求
    """
    response = db.table("tasks").select(
        "id, external_task_id, conversation_id, type, status, "
        "request_params, credits_locked, placeholder_message_id, "
        "started_at, last_polled_at"
    ).eq("user_id", current_user["id"]).in_(
        "status", ["pending", "running"]
    ).order("started_at", desc=False).execute()

    return {
        "tasks": response.data,
        "count": len(response.data),
    }


@router.post("/{external_task_id}/fail", summary="手动标记任务失败")
@limiter.limit("60/minute")
async def mark_task_failed(
    req: Request,
    current_user: CurrentUser,
    db: Database,
    external_task_id: str = Path(
        ...,
        regex=r"^[a-zA-Z0-9_-]{1,100}$",
        description="任务ID，只能包含字母、数字、下划线和连字符"
    ),
    request: MarkTaskFailedRequest = MarkTaskFailedRequest(),
) -> Dict[str, Any]:
    """
    手动标记任务为失败状态

    用于前端超时或用户主动取消任务。

    速率限制：每分钟最多 60 次请求
    """
    from datetime import datetime

    # 验证任务属于当前用户
    task = db.table("tasks").select("id").eq(
        "external_task_id", external_task_id
    ).eq("user_id", current_user["id"]).single().execute()

    if not task.data:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 更新状态
    db.table("tasks").update({
        "status": "failed",
        "error_message": request.reason,
        "completed_at": datetime.utcnow().isoformat(),
    }).eq("external_task_id", external_task_id).execute()

    return {"success": True, "message": "任务已标记为失败"}
