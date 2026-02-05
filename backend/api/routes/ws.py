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
)
from services.websocket_manager import HEARTBEAT_INTERVAL, ws_manager

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
            await ws_manager.send_to_connection(conn_id, build_subscribed_message(
                task_id=task_id,
                accumulated=result.get("accumulated", "") if result else "",
                current_index=result.get("current_index", -1) if result else -1
            ))

            # 补发错过的消息
            if result and result.get("missed_messages"):
                for idx, msg_json in result["missed_messages"]:
                    try:
                        msg = json.loads(msg_json)
                        await ws_manager.send_to_connection(conn_id, msg)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse missed message | index={idx}")

            logger.info(
                f"Task subscribed | conn={conn_id} | task={task_id} | "
                f"last_index={last_index} | "
                f"missed={len(result.get('missed_messages', [])) if result else 0}"
            )
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
