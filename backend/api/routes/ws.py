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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from schemas.websocket import (
    WSMessageType,
    build_error,
    build_ping,
    build_subscribed,
)
from services.websocket_manager import HEARTBEAT_INTERVAL, ws_manager
from services.websocket_auth import get_user_from_token, reject_websocket
from services.websocket_task_scope import find_task_in_connection_scope
from services.websocket_task_completion import (
    check_and_send_completed_task,
    get_task_accumulated_state,
)
from core.database import get_db
from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from core.org_scoped_db import OrgScopedDB

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="认证 token"),
    org_id: Optional[str] = Query(None, alias="org_id", description="企业ID"),
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
    user_id, error_type = await get_user_from_token(token)
    if not user_id:
        code = 4002 if error_type == "expired" else 4001
        reason = "Token expired" if error_type == "expired" else "Unauthorized"
        await reject_websocket(websocket, code=code, reason=reason)
        return

    # 1.5 验证 org_id 归属（防止伪造）
    verified_org_id = None
    if org_id:
        try:
            db = _build_connection_db(
                user_id, org_id, request_id="ws:handshake",
            )
            member = db.table("org_members").select("status").eq(
                "org_id", org_id
            ).eq("user_id", user_id).maybe_single().execute()
            if member and member.data and member.data.get("status") == "active":
                verified_org_id = org_id
            else:
                logger.warning(f"WS org_id rejected | user={user_id} | org_id={org_id}")
                await reject_websocket(
                    websocket,
                    code=4003,
                    reason="Organization access denied",
                )
                return
        except Exception as e:
            logger.warning(f"WS org_id verify failed | error={e}")
            await reject_websocket(
                websocket,
                code=4003,
                reason="Organization verification failed",
            )
            return

    # 2. 注册连接
    conn_id = await ws_manager.connect(websocket, user_id, org_id=verified_org_id)

    # 3. 启动心跳任务
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(conn_id, websocket)
    )

    try:
        # 4. 消息循环
        while True:
            try:
                data = await websocket.receive_json()
                await _handle_message(
                    conn_id,
                    user_id,
                    verified_org_id,
                    data,
                    _build_connection_db(
                        user_id,
                        verified_org_id,
                        request_id=f"ws:{conn_id}",
                    ),
                )
            except json.JSONDecodeError:
                await ws_manager.send_to_connection(conn_id, build_error(
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
                await websocket.send_json(build_ping())
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _handle_message(
    conn_id: str,
    user_id: str,
    org_id: str | None,
    data: dict,
    db: Any | None = None,
):
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
        await _handle_task_subscription(
            conn_id, user_id, org_id, payload.get("task_id"), db,
        )

    elif msg_type == WSMessageType.UNSUBSCRIBE.value:
        # 取消订阅
        task_id = payload.get("task_id")
        if task_id:
            await ws_manager.unsubscribe_task(conn_id, task_id)
            logger.info(f"Task unsubscribed | conn={conn_id} | task={task_id}")

    elif msg_type == WSMessageType.TOOL_CONFIRM_RESPONSE.value:
        confirmation_id = payload.get("confirmation_id")
        if not confirmation_id or "approved" not in payload:
            code = (
                "TOOL_CONFIRM_PROTOCOL_OBSOLETE"
                if payload.get("tool_call_id") else "MALFORMED_TOOL_CONFIRM_RESPONSE"
            )
            await ws_manager.send_to_connection(conn_id, build_error(
                "Tool confirmation response was rejected", code=code,
            ))
            return
        from pydantic import ValidationError
        from schemas.websocket import ToolConfirmResponsePayload
        try:
            response = ToolConfirmResponsePayload.model_validate(payload)
        except ValidationError:
            await ws_manager.send_to_connection(conn_id, build_error(
                "Tool confirmation response was rejected",
                code="MALFORMED_TOOL_CONFIRM_RESPONSE",
            ))
            return
        try:
            from services.tool_confirmation import tool_confirmation_service
            result = await tool_confirmation_service.consume_response(
                confirmation_id=response.confirmation_id, user_id=user_id,
                org_id=org_id, approved=response.approved,
            )
            if result.startswith("WON:"):
                ws_manager.forget_confirmation_delivery(
                    response.confirmation_id,
                )
        except Exception as exc:
            result = "CONFIRMATION_UNAVAILABLE"
            logger.warning(
                "tool_confirm_response_failed | error_code=CONFIRMATION_UNAVAILABLE | "
                f"exception_type={type(exc).__name__}"
            )
        if not result.startswith("WON:"):
            await ws_manager.send_to_connection(conn_id, build_error(
                "Tool confirmation response was rejected", code=result,
            ))

    elif msg_type == WSMessageType.USER_STEER.value:
        await _handle_user_steer(
            conn_id, user_id, org_id,
            payload.get("task_id"), payload.get("message", ""), db,
        )

    elif msg_type == WSMessageType.FORM_SUBMIT.value:
        # 用户在聊天中提交表单（定时任务创建/修改等）
        form_type = payload.get("form_type", "")
        form_data = payload.get("form_data", {})
        conversation_id = payload.get("conversation_id", "")
        if form_type and form_data:
            asyncio.create_task(_handle_form_submit(
                conn_id, user_id, org_id, form_type, form_data, conversation_id,
                db,
            ))
        else:
            await ws_manager.send_to_connection(conn_id, build_error(
                "form_type and form_data are required",
                code="MISSING_FORM_PARAMS",
            ))

    else:
        logger.warning(f"Unknown message type | conn={conn_id} | type={msg_type}")


async def _handle_task_subscription(
    conn_id: str,
    user_id: str,
    org_id: str | None,
    task_id: str | None,
    db: Any | None = None,
) -> None:
    """验证任务租户边界后建立订阅。"""
    if not task_id:
        await ws_manager.send_to_connection(conn_id, build_error(
            "task_id is required", code="MISSING_TASK_ID",
        ))
        return

    scoped_db = db or _build_connection_db(
        user_id, org_id, request_id=f"ws:{conn_id}:subscribe",
    )
    task = find_task_in_connection_scope(
        scoped_db, task_id, user_id, org_id,
    )
    if not task:
        logger.warning(
            f"WS task scope rejected | conn={conn_id} | "
            f"user={user_id} | org={org_id} | task={task_id}"
        )
        await ws_manager.send_to_connection(conn_id, build_error(
            "Task is not available in this tenant context",
            code="TASK_SCOPE_MISMATCH",
        ))
        return

    if not await ws_manager.subscribe_task(conn_id, task_id):
        await ws_manager.send_to_connection(conn_id, build_error(
            "Connection not found", code="CONN_NOT_FOUND",
        ))
        return

    accumulated, accumulated_blocks = get_task_accumulated_state(task)
    await ws_manager.send_to_connection(conn_id, build_subscribed(
        task_id=task_id,
        accumulated=accumulated or "",
        accumulated_blocks=accumulated_blocks or [],
        current_index=-1,
    ))
    logger.info(
        f"Task subscribed | conn={conn_id} | task={task_id} | "
        f"accumulated_len={len(accumulated or '')} | "
        f"blocks={len(accumulated_blocks or [])}"
    )
    await check_and_send_completed_task(conn_id, task_id, task, scoped_db)


async def _handle_user_steer(
    conn_id: str,
    user_id: str,
    org_id: str | None,
    task_id: str | None,
    message: str,
    db: Any | None = None,
) -> None:
    """验证任务租户边界后处理执行中追加消息。"""
    if not task_id or not message:
        await ws_manager.send_to_connection(conn_id, build_error(
            "task_id and message are required",
            code="MISSING_STEER_PARAMS",
        ))
        return

    scoped_db = db or _build_connection_db(
        user_id, org_id, request_id=f"ws:{conn_id}:steer",
    )
    task = find_task_in_connection_scope(
        scoped_db, task_id, user_id, org_id,
    )
    if not task:
        await ws_manager.send_to_connection(conn_id, build_error(
            "Task is not available in this tenant context",
            code="TASK_SCOPE_MISMATCH",
        ))
        return

    resolved = ws_manager.resolve_steer(task_id, message, org_id=org_id)
    logger.info(
        f"User steer | conn={conn_id} | task={task_id} | "
        f"msg={message[:50]} | resolved={resolved}"
    )


async def _handle_form_submit(
    conn_id: str,
    user_id: str,
    org_id: str | None,
    form_type: str,
    form_data: Dict[str, Any],
    conversation_id: str,
    db: Any | None = None,
) -> None:
    """处理表单提交（异步任务）"""
    import time as _time
    from services.scheduler.chat_task_manager import handle_form_submit

    try:
        if not org_id:
            await ws_manager.send_to_connection(conn_id, {
                "type": WSMessageType.FORM_SUBMIT_RESULT.value,
                "payload": {"success": False, "message": "未找到企业信息"},
                "conversation_id": conversation_id,
                "timestamp": int(_time.time() * 1000),
            })
            return

        result = await handle_form_submit(
            db or _build_connection_db(
                user_id, org_id, request_id=f"ws:{conn_id}:form",
            ),
            user_id,
            org_id,
            form_type,
            form_data,
        )

        await ws_manager.send_to_connection(conn_id, {
            "type": WSMessageType.FORM_SUBMIT_RESULT.value,
            "payload": result,
            "conversation_id": conversation_id,
            "timestamp": int(_time.time() * 1000),
        })

        logger.info(
            f"Form submitted | conn={conn_id} | type={form_type} | "
            f"success={result.get('success')}"
        )
    except Exception as e:
        logger.error(f"Form submit error | conn={conn_id} | type={form_type} | error={e}")
        await ws_manager.send_to_connection(conn_id, {
            "type": WSMessageType.FORM_SUBMIT_RESULT.value,
            "payload": {"success": False, "message": f"提交失败: {e}"},
            "conversation_id": conversation_id,
            "timestamp": int(_time.time() * 1000),
        })


def _build_connection_db(
    user_id: str,
    org_id: str | None,
    *,
    request_id: str,
) -> OrgScopedDB:
    """Build one immutable Runtime database identity for a WS operation."""
    scope = DatabaseScope(
        actor_user_id=user_id,
        org_id=org_id,
        access_kind=DatabaseAccessKind.RUNTIME,
        request_id=request_id[:128],
    )
    return OrgScopedDB(ScopedDatabaseClient(get_db(), scope), org_id)


# === 健康检查端点（用于负载均衡器检测 WebSocket 可用性）===
@router.get("/ws/health")
async def websocket_health():
    """WebSocket 服务健康检查"""
    stats = ws_manager.get_stats()
    return {
        "status": "healthy",
        "connections": stats["total_connections"],
        "users": stats["total_users"],
        "subscriptions": stats["total_subscriptions"],
    }
