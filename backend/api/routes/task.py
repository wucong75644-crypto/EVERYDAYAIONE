"""
任务管理路由

提供任务查询、恢复等接口
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Path, Request
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import CurrentUser, Database, OrgCtx, ScopedDB
from core.exceptions import (
    AppException,
    NotFoundError,
    ValidationError,
    PermissionDeniedError,
)
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
    ctx: OrgCtx,
    db: ScopedDB,
) -> Dict[str, Any]:
    """
    获取当前用户的活跃任务

    返回：
    - 进行中的任务 (status in ['pending', 'running'])
    - 最近 5 分钟内终结的任务 (status in ['completed', 'failed'])，包括所有类型

    速率限制：每分钟最多 30 次请求
    """
    try:
        cutoff_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        task_fields = (
            "id, external_task_id, client_task_id, conversation_id, type, status, "
            "request_params, credits_locked, placeholder_message_id, "
            "placeholder_created_at, started_at, last_polled_at, "
            "accumulated_content, accumulated_blocks, model_id, error_message, assistant_message_id"
        )

        # 查询进行中的任务（OrgScopedDB 自动加 org_id 过滤）
        pending_response = db.table("tasks").select(task_fields).eq(
            "user_id", ctx.user_id
        ).in_("status", ["pending", "running"]).order(
            "started_at", desc=False
        ).execute()

        # 查询最近 5 分钟内终结的任务
        recent_completed_response = db.table("tasks").select(task_fields).eq(
            "user_id", ctx.user_id
        ).in_("status", ["completed", "failed"]).gte(
            "completed_at", cutoff_time
        ).order("started_at", desc=False).execute()

        all_tasks = pending_response.data + recent_completed_response.data

        return {
            "tasks": all_tasks,
            "count": len(all_tasks),
        }
    except (
        ValidationError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Get pending tasks failed | user_id={ctx.user_id} | error={str(e)}"
        )
        raise AppException(
            code="GET_PENDING_TASKS_ERROR",
            message="获取任务列表失败",
            status_code=500,
        )


@router.get("/{task_id}/content", summary="获取聊天任务累积内容")
@limiter.limit("60/minute")
async def get_chat_task_content(
    request: Request,
    task_id: str,
    ctx: OrgCtx,
    db: ScopedDB,
) -> Dict[str, Any]:
    """获取 chat 类型任务的当前状态和累积内容"""
    try:
        q = db.table("tasks").select(
            "id, status, accumulated_content, error_message, completed_at, "
            "conversation_id, assistant_message_id"
        ).eq("id", task_id).eq("user_id", ctx.user_id)
        if ctx.org_id:
            q = q.eq("org_id", ctx.org_id)
        else:
            q = q.is_("org_id", "null")
        task = q.single().execute()

        if not task.data:
            raise NotFoundError(resource="任务", resource_id=task_id)

        return {
            "task_id": task_id,
            "status": task.data["status"],
            "accumulated_content": task.data.get("accumulated_content"),
            "error_message": task.data.get("error_message"),
            "completed_at": task.data.get("completed_at"),
            "conversation_id": task.data.get("conversation_id"),
            "assistant_message_id": task.data.get("assistant_message_id"),
        }
    except (
        ValidationError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Get chat task content failed | task_id={task_id} | "
            f"user_id={ctx.user_id} | error={str(e)}"
        )
        raise AppException(
            code="GET_CHAT_TASK_CONTENT_ERROR",
            message="获取任务内容失败",
            status_code=500,
        )


@router.post("/cancel-by-message/{message_id}", summary="通过消息ID取消关联任务")
@limiter.limit("60/minute")
async def cancel_task_by_message_id(
    request: Request,
    ctx: OrgCtx,
    db: ScopedDB,
    message_id: str = Path(
        ...,
        regex=r"^[a-zA-Z0-9_-]{1,100}$",
        description="消息ID（占位符消息或助手消息）"
    ),
) -> Dict[str, Any]:
    """
    通过消息 ID 取消关联的后台任务
    """
    try:
        for field in ("placeholder_message_id", "assistant_message_id"):
            q = db.table("tasks").select("id, external_task_id").eq(
                field, message_id
            ).eq("user_id", ctx.user_id).in_(
                "status", ["pending", "running"]
            )
            if ctx.org_id:
                q = q.eq("org_id", ctx.org_id)
            else:
                q = q.is_("org_id", "null")
            result = q.execute()

            if result.data:
                from services.websocket_manager import ws_manager

                for task in result.data:
                    db.table("tasks").update({
                        "status": "failed",
                        "error_message": "用户取消了任务",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", task["id"]).execute()

                    # 向运行中的 Agent 循环发送取消信号（同进程 asyncio.Event）
                    ext_id = task.get("external_task_id")
                    if ext_id:
                        ws_manager.cancel_task(ext_id)

                    logger.info(
                        f"Task cancelled by user | task_id={task['id']} | "
                        f"ext={ext_id} | message_id={message_id} | user_id={ctx.user_id}"
                    )

                # 同步更新消息状态，防止重新登录后仍为 streaming 显示空气泡
                db.table("messages").update({
                    "status": "completed",
                }).eq("id", message_id).execute()

                return {"success": True, "cancelled_count": len(result.data)}

        return {"success": True, "cancelled_count": 0}
    except (
        ValidationError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Cancel task by message_id failed | message_id={message_id} | "
            f"user_id={ctx.user_id} | error={str(e)}"
        )
        raise AppException(
            code="CANCEL_TASK_BY_MESSAGE_ERROR",
            message="取消任务失败",
            status_code=500,
        )


@router.post("/{external_task_id}/fail", summary="手动标记任务失败")
@limiter.limit("60/minute")
async def mark_task_failed(
    req: Request,
    ctx: OrgCtx,
    db: ScopedDB,
    external_task_id: str = Path(
        ...,
        regex=r"^[a-zA-Z0-9_-]{1,100}$",
        description="任务ID，只能包含字母、数字、下划线和连字符"
    ),
    request: MarkTaskFailedRequest = MarkTaskFailedRequest(),
) -> Dict[str, Any]:
    """
    手动标记任务为失败状态

    速率限制：每分钟最多 60 次请求
    """
    try:
        # 验证任务属于当前用户 + org 隔离
        q = db.table("tasks").select("id").eq(
            "external_task_id", external_task_id
        ).eq("user_id", ctx.user_id)
        if ctx.org_id:
            q = q.eq("org_id", ctx.org_id)
        else:
            q = q.is_("org_id", "null")
        task = q.single().execute()

        if not task.data:
            raise NotFoundError(resource="任务", resource_id=external_task_id)

        # 更新状态（带 user_id 过滤防越权）
        db.table("tasks").update({
            "status": "failed",
            "error_message": request.reason,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("external_task_id", external_task_id).eq(
            "user_id", ctx.user_id
        ).execute()

        return {"success": True, "message": "任务已标记为失败"}
    except (
        ValidationError,
        NotFoundError,
        PermissionDeniedError,
        AppException,
    ):
        raise
    except Exception as e:
        logger.error(
            f"Mark task failed error | external_task_id={external_task_id} | "
            f"user_id={ctx.user_id} | reason={request.reason} | error={str(e)}"
        )
        raise AppException(
            code="MARK_TASK_FAILED_ERROR",
            message="标记任务失败时出错",
            status_code=500,
        )
