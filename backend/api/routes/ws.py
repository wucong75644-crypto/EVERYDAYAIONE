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
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from core.security import decode_access_token
from core.exceptions import TokenExpiredError
from schemas.websocket import (
    WSMessageType,
    build_error,
    build_ping,
    build_subscribed,
    build_message_done,
    build_message_error,
    build_control_result,
)
from services.websocket_manager import HEARTBEAT_INTERVAL, ws_manager
from core.database import get_db
from core.database import get_async_db
from services.conversation_command_store import DatabaseConversationCommandStore
from services.conversation_commands import CommandType
from services.conversation_control_router import ControlAction, ConversationControlRouter
from services.conversation_control_service import (
    execute_control_action,
    load_control_tasks,
)

router = APIRouter(tags=["WebSocket"])


async def get_user_from_token(token: str) -> tuple[Optional[str], str]:
    """
    从 token 获取用户 ID

    Args:
        token: JWT token

    Returns:
        (用户 ID, 错误类型)。成功时 error_type 为空字符串
    """
    try:
        payload = decode_access_token(token)
        return payload.get("sub"), ""
    except TokenExpiredError:
        logger.debug("Token expired")
        return None, "expired"
    except Exception as e:
        logger.warning(f"Token invalid | error={e}")
        return None, "invalid"


# TODO(time-context PR3): WebSocket 入口注入 RequestContext，全链路请求级 SSOT
# 目前 ERPAgent / ChatHandler 内部用 RequestContext.build() fallback，时区正确。
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B15)
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
        await websocket.close(code=code, reason=reason)
        return

    # 1.5 验证 org_id 归属（防止伪造）
    verified_org_id = None
    if org_id:
        try:
            db = get_db()
            member = db.table("org_members").select("status").eq(
                "org_id", org_id
            ).eq("user_id", user_id).maybe_single().execute()
            if member and member.data and member.data.get("status") == "active":
                verified_org_id = org_id
            else:
                logger.warning(f"WS org_id rejected | user={user_id} | org_id={org_id}")
        except Exception as e:
            logger.warning(f"WS org_id verify failed | error={e}")

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
                await _handle_message(conn_id, user_id, data, verified_org_id)
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
    data: dict,
    org_id: str | None = None,
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
        # 订阅任务
        task_id = payload.get("task_id")

        if task_id:
            success = await ws_manager.subscribe_task(conn_id, task_id)

            if success:
                last_seq = payload.get("last_delivery_seq", payload.get("last_index", 0))
                try:
                    last_seq = max(int(last_seq), 0)
                except (TypeError, ValueError):
                    last_seq = 0
                delivery_state = await _get_task_delivery_state(
                    task_id, user_id, last_seq,
                )
                accumulated = delivery_state.get("snapshot_content")
                accumulated_blocks = delivery_state.get("snapshot_blocks")

                await ws_manager.send_to_connection(conn_id, build_subscribed(
                    task_id=task_id,
                    accumulated=accumulated or "",
                    accumulated_blocks=accumulated_blocks or [],
                    current_index=-1,
                    message_id=delivery_state.get("message_id"),
                    delivery_session_id=delivery_state.get("session_id"),
                    stream_id=delivery_state.get("stream_id"),
                    execution_attempt=delivery_state.get("execution_attempt"),
                    delivery_status=delivery_state.get("delivery_status"),
                    next_seq=delivery_state.get("next_seq"),
                    snapshot_seq=delivery_state.get("snapshot_seq"),
                    events=delivery_state.get("events") or [],
                ))

                logger.info(f"Task subscribed | conn={conn_id} | task={task_id} | accumulated_len={len(accumulated or '')} | blocks={len(accumulated_blocks or [])}")

                # 检查任务是否已完成（解决订阅晚于任务完成的问题）
                await _check_and_send_completed_task(conn_id, task_id, user_id)
            else:
                await ws_manager.send_to_connection(conn_id, build_error(
                    "Connection not found",
                    code="CONN_NOT_FOUND"
                ))
        else:
            await ws_manager.send_to_connection(conn_id, build_error(
                "task_id is required",
                code="MISSING_TASK_ID"
            ))

    elif msg_type == WSMessageType.UNSUBSCRIBE.value:
        # 取消订阅
        task_id = payload.get("task_id")
        if task_id:
            await ws_manager.unsubscribe_task(conn_id, task_id)
            logger.info(f"Task unsubscribed | conn={conn_id} | task={task_id}")

    elif msg_type == WSMessageType.TOOL_CONFIRM_RESPONSE.value:
        # 用户确认/拒绝写操作
        tool_call_id = payload.get("tool_call_id")
        approved = payload.get("approved", False)
        task_id = payload.get("task_id")
        conversation_id = payload.get("conversation_id")
        if tool_call_id and task_id and conversation_id:
            persisted, error_code = await _persist_actor_tool_confirmation(
                user_id=user_id,
                task_id=str(task_id),
                conversation_id=str(conversation_id),
                tool_call_id=str(tool_call_id),
                approved=bool(approved),
            )
            if not persisted and error_code == "CONFIRM_LEGACY":
                # 仍由旧 ChatGenerateMixin 管理的任务：上下文用于本地 scope 校验，
                # 但不强制要求其已升级到 Actor 控制事件表。
                resolved = ws_manager.resolve_confirm(
                    str(tool_call_id),
                    bool(approved),
                    task_id=str(task_id),
                    conversation_id=str(conversation_id),
                )
                logger.info(
                    f"Legacy scoped tool confirm response | conn={conn_id} | "
                    f"tool_call_id={tool_call_id} | resolved={resolved}"
                )
                return
            if not persisted:
                await ws_manager.send_to_connection(conn_id, build_error(
                    "Confirmation scope is invalid or task is no longer running",
                    code=error_code,
                ))
                return
            # 同进程的传统等待器立即唤醒；跨进程执行器会从 PostgreSQL 事件恢复。
            resolved = ws_manager.resolve_confirm(
                str(tool_call_id),
                bool(approved),
                task_id=str(task_id),
                conversation_id=str(conversation_id),
            )
            logger.info(
                f"Tool confirm response | conn={conn_id} | "
                f"tool_call_id={tool_call_id} | approved={approved} | "
                f"resolved={resolved} | persisted={persisted}"
            )
        elif tool_call_id:
            # 老客户端/非 Actor 工具仍走原有内存协议；Actor 前端会发送完整上下文。
            resolved = ws_manager.resolve_confirm(tool_call_id, bool(approved))
            logger.info(
                f"Legacy tool confirm response | conn={conn_id} | "
                f"tool_call_id={tool_call_id} | approved={approved} | "
                f"resolved={resolved}"
            )
        else:
            await ws_manager.send_to_connection(conn_id, build_error(
                "tool_call_id is required",
                code="MISSING_TOOL_CALL_ID",
            ))

    elif msg_type == WSMessageType.USER_STEER.value:
        # 用户在 AI 执行中发送新消息（打断当前工具循环）
        task_id = payload.get("task_id")
        message = payload.get("message", "")
        if task_id and message:
            conversation_id = payload.get("conversation_id")
            control_tasks = None
            if conversation_id:
                control_tasks = load_control_tasks(
                    get_db(),
                    conversation_id=str(conversation_id),
                    user_id=user_id,
                    org_id=org_id,
                )
            candidate_task = control_tasks.running or control_tasks.paused if control_tasks else None
            candidate_ids = {
                str(candidate_task.get(field))
                for field in ("id", "client_task_id", "external_task_id")
                if candidate_task and candidate_task.get(field)
            }
            if (
                control_tasks
                and candidate_task
                and str(task_id) in candidate_ids
            ):
                decision = await ConversationControlRouter().route(
                    str(message),
                    control_tasks.to_router_state(),
                    org_id=org_id,
                )
                if decision.action is not ControlAction.NONE:
                    result = execute_control_action(
                        get_db(),
                        tasks=control_tasks,
                        action=decision.action,
                        user_id=user_id,
                        org_id=org_id,
                    )
                    push_task_id = result.get("client_task_id") or result.get("external_task_id")
                    if decision.action in {ControlAction.PAUSE, ControlAction.CANCEL} and push_task_id:
                        ws_manager.cancel_task(str(push_task_id), org_id=org_id)
                    if decision.action is ControlAction.RESUME:
                        if push_task_id:
                            await ws_manager.clear_cancelled_gate(
                                str(push_task_id), org_id=org_id,
                            )
                        from services.conversation_worker import RedisConversationWakeup
                        await RedisConversationWakeup().publish(
                            str(conversation_id), org_id,
                        )
                    await ws_manager.send_to_connection(conn_id, build_control_result(
                        action=decision.action.value,
                        outcome=str(result.get("outcome") or "unknown"),
                        conversation_id=str(conversation_id),
                        task_id=push_task_id or result.get("task_id"),
                        message_id=result.get("assistant_message_id"),
                        client_task_id=result.get("client_task_id"),
                        external_task_id=result.get("external_task_id"),
                    ))
                    return
            resolved = ws_manager.resolve_steer(task_id, message)
            logger.info(
                f"User steer | conn={conn_id} | task={task_id} | "
                f"msg={message[:50]} | resolved={resolved}"
            )
        else:
            await ws_manager.send_to_connection(conn_id, build_error(
                "task_id and message are required",
                code="MISSING_STEER_PARAMS",
            ))


    elif msg_type == WSMessageType.FORM_SUBMIT.value:
        # 用户在聊天中提交或取消表单。form_id/message_id/conversation_id 是
        # 持久化交互状态的定位键，不能再依赖浏览器本地组件状态。
        form_type = payload.get("form_type", "")
        form_data = payload.get("form_data", {})
        conversation_id = payload.get("conversation_id", "")
        message_id = payload.get("message_id", "")
        form_id = payload.get("form_id", "")
        action = payload.get("action", "submit")
        if (
            form_type
            and isinstance(form_data, dict)
            and conversation_id
            and message_id
            and form_id
            and action in {"submit", "cancel"}
        ):
            asyncio.create_task(_handle_form_submit(
                conn_id, user_id, form_type, form_data, conversation_id,
                message_id, form_id, action,
            ))
        else:
            await ws_manager.send_to_connection(conn_id, build_error(
                "form interaction identifiers are required",
                code="MISSING_FORM_PARAMS",
            ))

    else:
        logger.warning(f"Unknown message type | conn={conn_id} | type={msg_type}")


async def _persist_actor_tool_confirmation(
    *,
    user_id: str,
    task_id: str,
    conversation_id: str,
    tool_call_id: str,
    approved: bool,
) -> tuple[bool, str]:
    """校验审批归属并写入 PostgreSQL；非 Actor 任务不进入控制事件表。"""
    try:
        db = await get_async_db()
        task = await _find_task_for_confirmation(db, task_id, user_id)
        if not task:
            return False, "CONFIRM_TASK_NOT_FOUND"
        is_actor = (
            task.get("type") == "chat"
            and isinstance(task.get("delivery_context"), dict)
            and task["delivery_context"].get("actor")
        )
        if not is_actor:
            return False, "CONFIRM_LEGACY"
        if task.get("conversation_id") != conversation_id:
            return False, "CONFIRM_SCOPE_INVALID"
        if task.get("status") != "running":
            return False, "CONFIRM_SCOPE_INVALID"

        result = await DatabaseConversationCommandStore(db).append(
            conversation_id=conversation_id,
            task_id=str(task["id"]),
            turn_id=str(task["turn_id"]) if task.get("turn_id") else None,
            command_type=CommandType.APPROVAL_RESULT,
            dedupe_key=f"approval:{tool_call_id}",
            payload={
                "tool_call_id": tool_call_id,
                "approved": approved,
                "user_id": user_id,
            },
        )
        # 第一个响应胜出；同值重试幂等，冲突响应不覆盖原决定。
        if result.get("already_enqueued") is True:
            existing = result.get("payload")
            if not isinstance(existing, dict) or bool(existing.get("approved")) != approved:
                return False, "CONFIRM_SCOPE_INVALID"
        return True, ""
    except Exception as error:
        logger.warning(
            "Actor tool confirmation persistence failed | "
            f"task_id={task_id} | error={type(error).__name__}"
        )
        return False, "CONFIRM_PERSIST_FAILED"


async def _find_task_for_confirmation(
    db: Any,
    task_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """按前端可能使用的任务标识查找任务，避免直接把非 UUID cast 成 id。"""
    for field in ("client_task_id", "external_task_id"):
        result = await db.table("tasks").select("*").eq(
            field, task_id
        ).eq("user_id", user_id).maybe_single().execute()
        if result and result.data:
            return result.data
    try:
        uuid.UUID(task_id)
    except (ValueError, AttributeError):
        return None
    result = await db.table("tasks").select("*").eq(
        "id", task_id
    ).eq("user_id", user_id).maybe_single().execute()
    return result.data if result and result.data else None


async def _handle_form_submit(
    conn_id: str,
    user_id: str,
    form_type: str,
    form_data: Dict[str, Any],
    conversation_id: str,
    message_id: str,
    form_id: str,
    action: str = "submit",
) -> None:
    """处理持久化聊天表单交互；同一表单只允许一次有效状态转换。"""
    import time as _time
    from services.scheduler.chat_task_manager import handle_form_submit

    async def send_result(result: Dict[str, Any]) -> None:
        payload = {
            **result,
            "form_id": form_id,
            "message_id": message_id,
            "conversation_id": conversation_id,
            "action": action,
        }
        await ws_manager.send_to_connection(conn_id, {
            "type": WSMessageType.FORM_SUBMIT_RESULT.value,
            "payload": payload,
            "conversation_id": conversation_id,
            "timestamp": int(_time.time() * 1000),
        })

    def transition(
        db: Any,
        expected: str,
        next_status: str,
        *,
        result_message: str | None = None,
        next_form: Dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> Dict[str, Any]:
        response = db.rpc("transition_chat_form_state", {
            "p_message_id": message_id,
            "p_conversation_id": conversation_id,
            "p_form_id": form_id,
            "p_expected_status": expected,
            "p_next_status": next_status,
            "p_result_message": result_message,
            "p_next_form": next_form,
            "p_error_message": error_message,
        }).execute()
        payload = response.data if response else None
        if not isinstance(payload, dict):
            raise RuntimeError("CHAT_FORM_STATE_RESULT_INVALID")
        return payload

    db: Any | None = None
    submission_claimed = False
    try:
        db = get_db()

        # 查用户的 org_id
        member = db.table("org_members") \
            .select("org_id") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .limit(1) \
            .execute()
        org_id = member.data[0]["org_id"] if member.data else None

        if not org_id:
            await send_result({"success": False, "message": "未找到企业信息"})
            return

        # 先验证用户是该对话的所有者，RPC 再验证消息与表单精确匹配。
        from services.conversation_service import ConversationService
        await ConversationService(db).get_conversation(conversation_id, user_id, org_id)

        if action == "cancel":
            cancellation = transition(db, "open", "cancelled")
            if cancellation.get("outcome") == "state_conflict":
                if cancellation.get("status") == "cancelled":
                    await send_result({"success": True, "message": "表单已取消", "status": "cancelled"})
                    return
                await send_result({"success": False, "message": "表单状态已变化，请刷新后查看"})
                return
            if cancellation.get("outcome") != "transitioned":
                await send_result({"success": False, "message": "取消失败：表单不存在或已失效"})
                return
            await send_result({"success": True, "message": "表单已取消", "status": "cancelled"})
            return

        claimed = transition(db, "open", "submitting")
        if claimed.get("outcome") == "state_conflict":
            status = claimed.get("status")
            if status == "submitted":
                await send_result({"success": True, "message": "该表单已提交", "status": status})
            elif status == "submitting":
                await send_result({"success": False, "message": "表单正在处理中，请稍候", "status": status})
            else:
                await send_result({"success": False, "message": "表单已取消，请重新发起任务", "status": status})
            return
        if claimed.get("outcome") != "transitioned":
            await send_result({"success": False, "message": "提交失败：表单不存在或已失效"})
            return
        submission_claimed = True

        result = await handle_form_submit(db, user_id, org_id, form_type, form_data)
        if result.get("success"):
            resolved = transition(
                db,
                "submitting",
                "submitted",
                result_message=str(result.get("message") or ""),
                next_form=result.get("next_form") if isinstance(result.get("next_form"), dict) else None,
            )
            if resolved.get("outcome") != "transitioned":
                raise RuntimeError("CHAT_FORM_STATE_COMMIT_FAILED")
            submission_claimed = False
        else:
            restored = transition(
                db,
                "submitting",
                "open",
                error_message=str(result.get("message") or "提交失败，请重试"),
            )
            if restored.get("outcome") != "transitioned":
                raise RuntimeError("CHAT_FORM_STATE_RESTORE_FAILED")
            submission_claimed = False

        await send_result(result)

        logger.info(
            f"Form submitted | conn={conn_id} | type={form_type} | "
            f"success={result.get('success')}"
        )
    except Exception as e:
        logger.error(f"Form submit error | conn={conn_id} | type={form_type} | error={e}")
        if db is not None and submission_claimed:
            try:
                transition(
                    db,
                    "submitting",
                    "open",
                    error_message="服务处理异常，请重试",
                )
            except Exception as transition_error:
                logger.error(
                    "Form state recovery failed | form={} | error={}",
                    form_id,
                    transition_error,
                )
        await send_result({"success": False, "message": f"提交失败: {e}"})


async def _get_task_accumulated_state(task_id: str, user_id: str) -> Tuple[Optional[str], Optional[list]]:
    """
    查询任务的累积内容和结构化内容块（用于 subscribe 时返回最新状态）

    仅查询 running 状态的 chat 任务，已完成的由 _check_and_send_completed_task 处理

    Returns:
        (accumulated_content, accumulated_blocks)
    """
    try:
        db = get_db()
        for field in ["external_task_id", "client_task_id"]:
            result = db.table("tasks").select(
                "accumulated_content, accumulated_blocks"
            ).eq(field, task_id).eq(
                "user_id", user_id
            ).eq("type", "chat").eq(
                "status", "running"
            ).maybe_single().execute()

            if result and result.data:
                content = result.data.get("accumulated_content")
                blocks = result.data.get("accumulated_blocks")
                if content or blocks:
                    return content, blocks or []
        return None, None
    except Exception as e:
        logger.warning(f"Failed to get accumulated_state | task_id={task_id} | error={e}")
        return None, None


async def _get_task_delivery_state(
    task_id: str,
    user_id: str,
    last_seq: int,
) -> dict[str, Any]:
    """读取交付会话快照；没有新协议数据时回退到 tasks 快照。"""
    try:
        db = get_db()
        task = await _find_task_by_any_id(db, task_id, user_id)
        if not task or task.get("type") != "chat":
            return {}
        response = db.rpc(
            "read_conversation_delivery_state",
            {
                "p_task_id": str(task["id"]),
                "p_user_id": user_id,
                "p_last_seq": last_seq,
            },
        ).execute()
        data = response.data if response else None
        if isinstance(data, dict) and data.get("outcome") in {"found", "empty"}:
            if data.get("outcome") == "found":
                return {
                    **data,
                    "message_id": task.get("assistant_message_id")
                    or task.get("placeholder_message_id"),
                }
            return {
                "snapshot_content": task.get("accumulated_content") or "",
                "snapshot_blocks": task.get("accumulated_blocks") or [],
                "delivery_status": task.get("status"),
                "message_id": task.get("assistant_message_id")
                or task.get("placeholder_message_id"),
            }
        return {
            "snapshot_content": task.get("accumulated_content") or "",
            "snapshot_blocks": task.get("accumulated_blocks") or [],
            "delivery_status": task.get("status"),
            "message_id": task.get("assistant_message_id")
            or task.get("placeholder_message_id"),
        }
    except Exception as error:
        logger.warning(
            f"Failed to read delivery_state | task_id={task_id} | error={error}"
        )
        accumulated, blocks = await _get_task_accumulated_state(task_id, user_id)
        return {
            "snapshot_content": accumulated or "",
            "snapshot_blocks": blocks or [],
            "message_id": None,
        }
async def _check_and_send_completed_task(conn_id: str, task_id: str, user_id: str):
    """
    检查任务是否已完成，如果已完成则推送完成消息

    简化版：只负责查询和推送，不修改数据库或扣除积分
    （数据库更新和积分扣除由 Handler.on_complete 负责）

    解决问题：前端订阅时任务可能已完成，需要补发完成消息
    """
    try:
        db = get_db()

        # 1. 查询任务（支持 id 或 external_task_id）
        task = await _find_task_by_any_id(db, task_id, user_id)
        if not task:
            logger.debug(f"Task not found for subscription check | task_id={task_id}")
            return

        status = task.get("status")
        if status not in ["completed", "failed", "cancelled"]:
            logger.debug(f"Task not in final state | task_id={task_id} | status={status}")
            return

        # 2. 获取关键字段
        task_type = task.get("type")
        conversation_id = task.get("conversation_id")
        message_id = task.get("assistant_message_id") or task.get("placeholder_message_id")

        # 🔥 优先使用 client_task_id（前端订阅的 ID）
        push_task_id = task.get("client_task_id") or task.get("external_task_id") or task_id

        logger.debug(
            f"Checking completed task | task_id={task_id} | push_task_id={push_task_id} | type={task_type} | status={status}"
        )

        # 3. 查询消息（优先使用数据库中的消息）
        message_data = await _find_message_by_id(db, message_id)
        if not message_data:
            # 如果消息不存在（极端情况），构建基础消息
            message_data = _build_fallback_message(task, message_id, conversation_id)

        # 4. 推送消息（使用 push_task_id 确保前端能收到）
        if status == "completed":
            await ws_manager.send_to_connection(conn_id, build_message_done(
                task_id=push_task_id,  # 🔥 使用 client_task_id
                conversation_id=conversation_id or "",
                message=message_data,
                credits_consumed=message_data.get("credits_cost", 0),
            ))
            logger.info(
                f"Sent completed {task_type} task | conn={conn_id} | push_task_id={push_task_id} | "
                f"message_id={message_id}"
            )
        elif status == "failed":
            await ws_manager.send_to_connection(conn_id, build_message_error(
                task_id=push_task_id,  # 🔥 使用 client_task_id
                conversation_id=conversation_id or "",
                message_id=message_id,
                error_code="GENERATION_FAILED",
                error_message=task.get("error_message", "生成失败"),
            ))
            logger.info(f"Sent failed {task_type} task | conn={conn_id} | task={task_id}")
        else:
            await ws_manager.send_to_connection(conn_id, build_message_error(
                task_id=push_task_id,
                conversation_id=conversation_id or "",
                message_id=message_id,
                error_code="TASK_CANCELLED",
                error_message="任务已取消",
            ))

    except Exception as e:
        logger.warning(f"Failed to check completed task | task={task_id} | error={e}")


async def _find_task_by_any_id(db, task_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    查询任务（支持 id、external_task_id 或 client_task_id）

    查询优先级：
    1. client_task_id（前端生成的 ID，用于乐观订阅）
    2. external_task_id（KIE 等第三方 API 返回的 ID）
    3. id（chat 任务的主键 ID）
    """
    # 1. 先尝试用 client_task_id 查询（乐观订阅场景）
    result = db.table("tasks").select("*").eq(
        "client_task_id", task_id
    ).eq("user_id", user_id).maybe_single().execute()

    if result and result.data:
        return result.data

    # 2. 再尝试用 external_task_id 查询（image/video 任务）
    result = db.table("tasks").select("*").eq(
        "external_task_id", task_id
    ).eq("user_id", user_id).maybe_single().execute()

    if result and result.data:
        return result.data

    # 3. 最后尝试用 id 查询（chat 任务）
    result = db.table("tasks").select("*").eq(
        "id", task_id
    ).eq("user_id", user_id).maybe_single().execute()

    return result.data if (result and result.data) else None


async def _find_message_by_id(db, message_id: str) -> Optional[Dict[str, Any]]:
    """查询消息"""
    if not message_id:
        return None

    result = db.table("messages").select("*").eq(
        "id", message_id
    ).maybe_single().execute()

    return result.data if (result and result.data) else None


def _build_fallback_message(task: Dict[str, Any], message_id: str, conversation_id: str) -> Dict[str, Any]:
    """
    构建基础消息（当数据库消息不存在时）

    注意：这是极端情况的降级方案，正常情况下消息应该已由 Handler 创建
    """
    task_type = task.get("type")

    # 根据任务类型构建内容
    if task_type == "chat":
        blocks = task.get("accumulated_blocks") or []
        text = task.get("accumulated_content", "")
        if blocks:
            from services.task_utils import merge_blocks_with_text
            content = merge_blocks_with_text(blocks, text)
        else:
            content = [{"type": "text", "text": text}]
    elif task_type == "image":
        urls = task.get("result", {}).get("image_urls", [])
        content = [{"type": "image", "url": url} for url in urls]
    elif task_type == "video":
        video_url = task.get("result", {}).get("video_url")
        content = [{"type": "video", "url": video_url}] if video_url else []
    else:
        content = []

    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": content,
        "status": "completed",
        "credits_cost": task.get("credits_locked", 0),
    }


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
