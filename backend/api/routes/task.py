"""
任务管理路由

提供任务查询、恢复等接口
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
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


@router.get("/pending", summary="获取用户活跃任务")
@limiter.limit("30/minute")
async def get_pending_tasks(
    request: Request,
    current_user: CurrentUser,
    db: Database,
) -> Dict[str, Any]:
    """
    获取当前用户的活跃任务

    返回：
    - 进行中的任务 (status in ['pending', 'running'])
    - 最近 5 分钟内终结的任务 (status in ['completed', 'failed'])，包括所有类型

    这样设计的原因：
    - 页面刷新期间任务可能刚好完成/失败
    - 前端需要知道这些任务的最终状态，以便：
      - 聊天任务：避免用户消息"莫名其妙消失"
      - 媒体任务：清理缓存触发消息重新加载

    速率限制：每分钟最多 30 次请求
    """
    # 计算 5 分钟前的时间点
    cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    # 查询进行中的任务
    pending_response = db.table("tasks").select(
        "id, external_task_id, conversation_id, type, status, "
        "request_params, credits_locked, placeholder_message_id, "
        "placeholder_created_at, started_at, last_polled_at, "
        "accumulated_content, model_id, error_message, assistant_message_id"
    ).eq("user_id", current_user["id"]).in_(
        "status", ["pending", "running"]
    ).order("started_at", desc=False).execute()

    # 查询最近 5 分钟内终结的任务（包括所有类型）
    # 前端需要知道刷新期间完成的任务，以便清理缓存触发重新加载
    recent_completed_response = db.table("tasks").select(
        "id, external_task_id, conversation_id, type, status, "
        "request_params, credits_locked, placeholder_message_id, "
        "placeholder_created_at, started_at, last_polled_at, "
        "accumulated_content, model_id, error_message, assistant_message_id"
    ).eq("user_id", current_user["id"]).in_(
        "status", ["completed", "failed"]
    ).gte(
        "completed_at", cutoff_time
    ).order("started_at", desc=False).execute()

    # 合并结果
    all_tasks = pending_response.data + recent_completed_response.data

    return {
        "tasks": all_tasks,
        "count": len(all_tasks),
    }


@router.get("/{task_id}/content", summary="获取聊天任务累积内容")
@limiter.limit("60/minute")
async def get_chat_task_content(
    request: Request,
    task_id: str,
    current_user: CurrentUser,
    db: Database,
) -> Dict[str, Any]:
    """获取 chat 类型任务的当前状态和累积内容"""
    task = db.table("tasks").select(
        "id, status, accumulated_content, error_message, completed_at, "
        "conversation_id, assistant_message_id"
    ).eq("id", task_id).eq("user_id", current_user["id"]).single().execute()

    if not task.data:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "task_id": task_id,
        "status": task.data["status"],
        "accumulated_content": task.data.get("accumulated_content"),
        "error_message": task.data.get("error_message"),
        "completed_at": task.data.get("completed_at"),
        "conversation_id": task.data.get("conversation_id"),
        "assistant_message_id": task.data.get("assistant_message_id"),
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
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("external_task_id", external_task_id).execute()

    return {"success": True, "message": "任务已标记为失败"}
