"""
WebSocket 端点

功能:
- 认证（token 验证）
- 消息路由
- 心跳处理
- 错误处理
"""

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from core.security import decode_access_token
from schemas.websocket import (
    WSMessageType,
    build_error_message,
    build_ping_message,
    build_pong_message,
    build_subscribed_message,
    build_chat_done_message,
    build_chat_error_message,
)
from services.websocket_manager import HEARTBEAT_INTERVAL, ws_manager
from core.database import get_supabase_client

router = APIRouter(tags=["WebSocket"])


async def get_user_from_token(token: str) -> Optional[str]:
    """
    从 token 获取用户 ID

    Args:
        token: JWT token

    Returns:
        用户 ID，验证失败返回 None
    """
    try:
        payload = decode_access_token(token)
        return payload.get("sub")  # user_id
    except Exception as e:
        logger.warning(f"Token verification failed | error={e}")
        return None


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="认证 token")
):
    """
    WebSocket 主端点

    连接流程:
    1. 验证 token
    2. 注册连接
    3. 启动心跳任务
    4. 消息循环
    """
    # 1. 认证
    user_id = await get_user_from_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 2. 注册连接
    conn_id = await ws_manager.connect(websocket, user_id)

    # 3. 启动心跳任务
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(conn_id, websocket)
    )

    try:
        # 4. 消息循环
        while True:
            try:
                data = await websocket.receive_json()
                await _handle_message(conn_id, user_id, data)
            except json.JSONDecodeError:
                await ws_manager.send_to_connection(conn_id, build_error_message(
                    "Invalid JSON",
                    code="INVALID_JSON"
                ))
            except WebSocketDisconnect:
                raise  # 重新抛出，让外层 except 处理

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected normally | conn={conn_id}")
    except Exception as e:
        logger.error(f"WebSocket error | conn={conn_id} | error={e}")
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await ws_manager.disconnect(conn_id)


async def _heartbeat_loop(conn_id: str, websocket: WebSocket):
    """
    心跳循环

    定期发送 ping 消息，保持连接活跃，避免网关超时断开。
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json(build_ping_message())
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _handle_message(conn_id: str, user_id: str, data: dict):
    """
    处理客户端消息

    Args:
        conn_id: 连接 ID
        user_id: 用户 ID
        data: 消息数据
    """
    msg_type = data.get("type")
    payload = data.get("payload", {})

    if msg_type == WSMessageType.PONG.value:
        # 心跳响应
        await ws_manager.update_heartbeat(conn_id)

    elif msg_type == WSMessageType.SUBSCRIBE.value:
        # 订阅任务
        task_id = payload.get("task_id")
        last_index = payload.get("last_index", -1)

        if task_id:
            result = await ws_manager.subscribe_task(conn_id, task_id, last_index)

            # 发送订阅确认
            # 关键逻辑：
            # - last_index < 0（首次订阅）：发送 accumulated，不补发 missed_messages
            # - last_index >= 0（断点续传）：不发送 accumulated，只补发 missed_messages
            # 这样避免内容重复
            send_accumulated = ""
            if last_index < 0 and result:
                send_accumulated = result.get("accumulated", "")

            await ws_manager.send_to_connection(conn_id, build_subscribed_message(
                task_id=task_id,
                accumulated=send_accumulated,
                current_index=result.get("current_index", -1) if result else -1
            ))

            # 补发错过的消息（只在断点续传时，即 last_index >= 0）
            missed_count = 0
            if result and result.get("missed_messages") and last_index >= 0:
                for idx, msg_json in result["missed_messages"]:
                    try:
                        msg = json.loads(msg_json)
                        await ws_manager.send_to_connection(conn_id, msg)
                        missed_count += 1
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse missed message | index={idx}")

            logger.info(
                f"Task subscribed | conn={conn_id} | task={task_id} | "
                f"last_index={last_index} | "
                f"missed_sent={missed_count} | "
                f"accumulated_len={len(result.get('accumulated', '')) if result else 0}"
            )

            # 【关键】检查任务是否已完成
            # 如果任务已完成但缓冲区为空，需要从数据库获取完整消息并推送
            await _check_and_send_completed_task(conn_id, task_id, user_id)
        else:
            await ws_manager.send_to_connection(conn_id, build_error_message(
                "task_id is required",
                code="MISSING_TASK_ID"
            ))

    elif msg_type == WSMessageType.UNSUBSCRIBE.value:
        # 取消订阅
        task_id = payload.get("task_id")
        if task_id:
            await ws_manager.unsubscribe_task(conn_id, task_id)
            logger.info(f"Task unsubscribed | conn={conn_id} | task={task_id}")

    else:
        logger.warning(f"Unknown message type | conn={conn_id} | type={msg_type}")


async def _check_and_send_completed_task(conn_id: str, task_id: str, user_id: str):
    """
    检查任务是否已完成，如果已完成则推送完成消息

    解决问题：前端订阅时任务可能已完成，但缓冲区为空，导致收不到完成消息
    支持 chat、image、video 三种任务类型
    """
    try:
        db = get_supabase_client()

        # 查询任务状态
        # 注意：chat 任务使用 id 字段，image/video 任务使用 external_task_id 字段
        # 先尝试用 id 查询（chat 任务）
        task_response = db.table("tasks").select(
            "id, external_task_id, status, conversation_id, accumulated_content, error_message, "
            "assistant_message_id, type, result, credits_locked"
        ).eq("id", task_id).eq("user_id", user_id).maybe_single().execute()

        # 如果没找到，尝试用 external_task_id 查询（image/video 任务）
        if not task_response.data:
            task_response = db.table("tasks").select(
                "id, external_task_id, status, conversation_id, accumulated_content, error_message, "
                "assistant_message_id, type, result, credits_locked"
            ).eq("external_task_id", task_id).eq("user_id", user_id).maybe_single().execute()

        if not task_response.data:
            logger.debug(f"Task not found for subscription check | task_id={task_id}")
            return

        task = task_response.data
        task_type = task.get("type")
        status = task.get("status")
        conversation_id = task.get("conversation_id")

        logger.debug(
            f"Checking completed task | task_id={task_id} | type={task_type} | "
            f"status={status} | conversation_id={conversation_id}"
        )

        if task_type == "chat":
            # Chat 任务处理逻辑
            if status == "completed":
                message_id = task.get("assistant_message_id")
                content = task.get("accumulated_content", "")

                if message_id and conversation_id:
                    msg_response = db.table("messages").select(
                        "content, credits_cost, model"
                    ).eq("id", message_id).single().execute()

                    if msg_response.data:
                        content = msg_response.data.get("content", content)
                        credits = msg_response.data.get("credits_cost", 0)
                        model = msg_response.data.get("model", "unknown")
                    else:
                        credits = 0
                        model = "unknown"

                    await ws_manager.send_to_connection(conn_id, build_chat_done_message(
                        task_id=task_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        content=content,
                        credits_consumed=credits,
                        model=model,
                    ))

                    logger.info(
                        f"Sent completed chat task | conn={conn_id} | task={task_id} | "
                        f"message_id={message_id}"
                    )

            elif status == "failed":
                error_message = task.get("error_message", "生成失败，请重试")
                await ws_manager.send_to_connection(conn_id, build_chat_error_message(
                    task_id=task_id,
                    error=error_message,
                    conversation_id=conversation_id,
                ))
                logger.info(f"Sent failed chat task | conn={conn_id} | task={task_id}")

        elif task_type in ("image", "video"):
            # Image/Video 任务处理逻辑
            external_task_id = task.get("external_task_id")

            if status == "completed":
                result = task.get("result") or {}

                # 获取媒体 URL
                urls = None
                if task_type == "image":
                    urls = result.get("image_urls", [])
                elif task_type == "video":
                    video_url = result.get("video_url")
                    urls = [video_url] if video_url else []

                # 查询消息（与 chat 任务统一，使用 assistant_message_id）
                created_message = None
                message_id = task.get("assistant_message_id")

                if message_id:
                    # 优先使用 assistant_message_id 精确查询
                    msg_response = db.table("messages").select("*").eq(
                        "id", message_id
                    ).maybe_single().execute()

                    if msg_response.data:
                        created_message = msg_response.data
                        logger.debug(
                            f"Found message by assistant_message_id | message_id={message_id}"
                        )

                # 构建并发送 task_status 消息
                from schemas.websocket import build_task_status_message
                message = build_task_status_message(
                    task_id=external_task_id or task_id,
                    conversation_id=conversation_id or "",
                    status="completed",
                    media_type=task_type,
                    urls=urls,
                    credits_consumed=task.get("credits_locked", 0),
                    created_message=created_message,
                )
                await ws_manager.send_to_connection(conn_id, message)

                logger.info(
                    f"Sent completed {task_type} task | conn={conn_id} | "
                    f"task={external_task_id or task_id} | has_message={created_message is not None}"
                )

            elif status == "failed":
                error_message = task.get("error_message", "生成失败，请重试")
                from schemas.websocket import build_task_status_message
                message = build_task_status_message(
                    task_id=external_task_id or task_id,
                    conversation_id=conversation_id or "",
                    status="failed",
                    media_type=task_type,
                    error_message=error_message,
                )
                await ws_manager.send_to_connection(conn_id, message)
                logger.info(f"Sent failed {task_type} task | conn={conn_id} | task={task_id}")

    except Exception as e:
        logger.warning(f"Failed to check completed task | task={task_id} | error={e}")


# === 健康检查端点（用于负载均衡器检测 WebSocket 可用性）===

@router.get("/ws/health")
async def websocket_health():
    """WebSocket 服务健康检查"""
    stats = ws_manager.get_stats()
    return {
        "status": "healthy",
        "connections": stats["total_connections"],
        "users": stats["total_users"],
        "tasks": stats["total_tasks"],
    }
