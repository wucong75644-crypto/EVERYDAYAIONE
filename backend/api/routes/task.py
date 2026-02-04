"""
任务管理路由

提供任务查询、恢复等接口
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import CurrentUser, Database
from core.limiter import limiter
from services.chat_stream_manager import chat_stream_manager, HEARTBEAT_INTERVAL


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
        "placeholder_created_at, started_at, last_polled_at, "
        "accumulated_content, model_id, error_message, assistant_message_id"  # 新增 chat 任务字段
    ).eq("user_id", current_user["id"]).in_(
        "status", ["pending", "running"]
    ).order("started_at", desc=False).execute()

    return {
        "tasks": response.data,
        "count": len(response.data),
    }


@router.get("/{task_id}/stream", summary="恢复聊天任务流式连接")
async def resume_chat_stream(
    request: Request,
    task_id: str,
    current_user: CurrentUser,
    db: Database,
    last_received_index: int = Query(-1, description="上次收到的消息索引，用于断点续传"),
):
    """
    恢复 chat 类型任务的 SSE 连接

    - 支持断点续传：传入 last_received_index 参数，只接收该索引之后的消息
    - 如果任务进行中且有活跃队列，直接订阅（广播模式，支持多连接）
    - 如果任务进行中但无队列（后台协程在处理），轮询数据库
    - 如果任务已完成，返回完整内容
    - 如果任务失败，返回错误
    """
    # 验证任务所有权
    task = db.table("tasks").select("*").eq(
        "id", task_id
    ).eq("user_id", current_user["id"]).single().execute()

    if not task.data:
        raise HTTPException(status_code=404, detail="任务不存在")

    task_data = task.data

    if task_data["type"] != "chat":
        raise HTTPException(status_code=400, detail="只支持 chat 类型任务")

    async def generate_stream():
        connection_id = None

        try:
            # 1. 已完成：返回完整内容
            if task_data["status"] == "completed":
                # 使用预分配的消息 ID 获取消息
                assistant_message_id = task_data.get("assistant_message_id")
                if assistant_message_id:
                    msg = db.table("messages").select("*").eq(
                        "id", assistant_message_id
                    ).single().execute()
                    if msg.data:
                        yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': msg.data}})}\n\n"
                else:
                    # 兼容旧数据
                    messages = db.table("messages").select("*").eq(
                        "conversation_id", task_data["conversation_id"]
                    ).order("created_at", desc=True).limit(1).execute()
                    if messages.data:
                        yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': messages.data[0]}})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # 2. 已失败：返回错误
            if task_data["status"] == "failed":
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': task_data.get('error_message', '任务失败')}})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # 3. 进行中：尝试订阅队列或轮询
            # 【断点续传】传入 last_received_index，只接收该索引之后的消息
            queue, accumulated_content, connection_id, current_index = await chat_stream_manager.subscribe(
                task_id, last_received_index=last_received_index
            )

            if queue:
                # 有活跃队列，订阅广播（支持多连接，不会竞争）
                # 【断点续传】只有首次连接（last_received_index=-1）才发送累积内容
                # 重连时通过 buffer 补发，避免重复
                if accumulated_content and last_received_index < 0:
                    yield f"data: {json.dumps({'type': 'accumulated', 'data': {'text': accumulated_content, '_index': current_index}})}\n\n"

                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                        if item is None:
                            break
                        yield item
                    except asyncio.TimeoutError:
                        # 30s 心跳，避免网关超时
                        yield ": heartbeat\n\n"
            else:
                # 无活跃队列（可能是进程重启后），轮询数据库
                last_content_length = len(task_data.get("accumulated_content") or "")

                # 先发送已有内容
                if task_data.get("accumulated_content"):
                    yield f"data: {json.dumps({'type': 'accumulated', 'data': {'text': task_data['accumulated_content']}})}\n\n"

                poll_count = 0
                max_polls = 600  # 最多轮询 10 分钟（1秒/次）

                while poll_count < max_polls:
                    await asyncio.sleep(1)
                    poll_count += 1

                    # 每 30 秒发送心跳
                    if poll_count % HEARTBEAT_INTERVAL == 0:
                        yield ": heartbeat\n\n"

                    # 重新查询任务状态
                    updated_task = db.table("tasks").select(
                        "status, accumulated_content, error_message, assistant_message_id"
                    ).eq("id", task_id).single().execute()

                    if not updated_task.data:
                        break

                    status = updated_task.data["status"]
                    current_content = updated_task.data.get("accumulated_content") or ""

                    # 发送新增内容（增量）
                    if len(current_content) > last_content_length:
                        new_content = current_content[last_content_length:]
                        yield f"data: {json.dumps({'type': 'content', 'data': {'text': new_content}})}\n\n"
                        last_content_length = len(current_content)

                    # 检查是否完成
                    if status == "completed":
                        assistant_message_id = updated_task.data.get("assistant_message_id")
                        if assistant_message_id:
                            msg = db.table("messages").select("*").eq(
                                "id", assistant_message_id
                            ).single().execute()
                            if msg.data:
                                yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': msg.data}})}\n\n"
                        break

                    if status == "failed":
                        yield f"data: {json.dumps({'type': 'error', 'data': {'message': updated_task.data.get('error_message', '任务失败')}})}\n\n"
                        break

            yield "data: [DONE]\n\n"

        finally:
            # 清理订阅
            if connection_id:
                await chat_stream_manager.unsubscribe(task_id, connection_id)

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
