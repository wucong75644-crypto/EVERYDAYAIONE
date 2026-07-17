"""
统一消息路由

提供统一的消息生成入口 /messages/generate。
支持聊天、图片、视频等多种生成类型。
"""

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from loguru import logger

from api.deps import CurrentUser, CurrentUserId, Database, OrgCtx, ScopedDB, TaskLimitSvc
from core.limiter import limiter, RATE_LIMITS
from core.exceptions import AppException
from schemas.message import (
    DeleteMessageResponse,
    GenerateRequest,
    GenerateResponse,
    GenerationType,
    Message,
    MessageListResult,
    MessageOperation,
    MessageResponse,
    MessageSearchResult,
)
from services.message_service import MessageService
from services.conversation_service import ConversationService
from services.handlers import get_handler
from services.user_activity_service import record_user_activity
from services.message_idempotency_service import MessageIdempotencyService

from api.routes.message_generation_helpers import (
    handle_retry_operation,
    handle_regenerate_or_send_operation,
    handle_regenerate_single_operation,
    start_generation_task,
    create_user_message,
    finalize_image_request_failure,
    prepare_assistant_message,
)
from api.routes.message_turn_anchors import resolve_existing_turn_anchor
from api.routes.message_request_preparation import (
    prepare_generation_request,
    resolve_generation_context,
)

# 向后兼容别名：test_placeholder_to_db.py 使用了带下划线前缀的名称
_handle_retry_operation = handle_retry_operation
_handle_regenerate_or_send_operation = handle_regenerate_or_send_operation
_start_generation_task = start_generation_task
_create_user_message = create_user_message

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["消息"])
message_router = APIRouter(prefix="/messages", tags=["消息"])


def get_message_service(db: ScopedDB) -> MessageService:
    """获取消息服务实例（租户隔离）"""
    return MessageService(db)


def get_conversation_service(db: ScopedDB) -> ConversationService:
    """获取对话服务实例（租户隔离）"""
    return ConversationService(db)


# ============================================================
# 智能路由
# ============================================================


    # _resolve_generation_type / _legacy_resolve 已移除
    # Web 端路由简化为: infer_generation_type (关键词) + ChatHandler 工具循环
    # 企微端仍通过 wecom_ai_mixin 调用 AgentLoop


# ============================================================
# 用户反馈信号
# ============================================================


def _record_user_feedback_signal(
    db,
    user_id: str,
    operation: str,
    model: str | None,
    gen_type: str,
    original_message_id: str | None,
    conversation_id: str,
) -> None:
    """记录用户反馈信号到 knowledge_metrics（fire-and-forget）"""

    async def _do_record() -> None:
        try:
            # 从原消息提取原始模型
            original_model = None
            if original_message_id:
                try:
                    orig = db.table("messages").select(
                        "generation_params"
                    ).eq("id", original_message_id).maybe_single().execute()
                    if orig and orig.data:
                        import json as _json
                        gp = orig.data.get("generation_params") or {}
                        if isinstance(gp, str):
                            gp = _json.loads(gp)
                        original_model = gp.get("model")
                except Exception as e:
                    logger.warning(
                        f"Failed to get original model | msg_id={original_message_id} | {e}"
                    )

            from services.knowledge_service import record_metric
            await record_metric(
                task_type="user_feedback",
                model_id=model or "unknown",
                status="success",
                user_id=user_id,
                params={
                    "feedback_type": operation,
                    "original_model": original_model,
                    "new_model": model,
                    "original_task_type": gen_type,
                    "original_message_id": original_message_id,
                    "conversation_id": conversation_id,
                },
            )
        except Exception as e:
            logger.debug(f"User feedback signal record skipped | error={e}")

    asyncio.create_task(_do_record())


# ============================================================
# 统一消息生成 API
# ============================================================


@router.post("/generate", response_model=GenerateResponse, summary="统一消息生成")
@limiter.limit(RATE_LIMITS["message_stream"])
async def generate_message(
    request: Request,
    conversation_id: str,
    body: GenerateRequest,
    ctx: OrgCtx,
    db: ScopedDB,
    task_limit_service: TaskLimitSvc,
):
    """
    统一消息生成入口

    根据 generation_type 或 content 自动路由到对应 Handler：
    - chat: 流式聊天（WebSocket 推送）
    - image: 图片生成（异步任务）
    - video: 视频生成（异步任务）

    支持三种操作：
    - send: 发送新消息（创建用户消息 + 创建 AI 消息）
    - retry: 重试失败的 AI 消息（不创建用户消息 + 原地更新）
    - regenerate: 重新生成成功的 AI 消息（创建用户消息 + 创建 AI 消息）
    """
    user_id = ctx.user_id

    idempotency_service = MessageIdempotencyService(db, user_id, ctx.org_id)
    idempotency_claim = idempotency_service.claim(request, conversation_id, body)
    if idempotency_claim and idempotency_claim.replay_response:
        return idempotency_claim.replay_response

    # 1. 检查任务限制，获取 slot_id
    slot_id: str | None = None
    slot_handed_off = False
    try:
        if task_limit_service:
            slot_id = await task_limit_service.check_and_acquire(
                user_id, conversation_id, org_id=ctx.org_id,
            )

        if slot_id:
            if body.params is None:
                body.params = {}
            body.params["_task_slot_id"] = slot_id

        response = await _do_generate_message(
            request=request,
            conversation_id=conversation_id,
            body=body,
            ctx=ctx,
            db=db,
            user_id=user_id,
        )
        slot_handed_off = True
        idempotency_service.complete(idempotency_claim, response)
        return response
    except AppException as exc:
        idempotency_service.fail(idempotency_claim, exc)
        raise
    except Exception as exc:
        idempotency_service.fail_unexpected(idempotency_claim, exc)
        raise
    finally:
        if not slot_handed_off and slot_id and task_limit_service:
            await task_limit_service.release(
                user_id, conversation_id,
                org_id=ctx.org_id, slot_id=slot_id,
            )


async def _do_generate_message(
    request: Request,
    conversation_id: str,
    body: GenerateRequest,
    ctx: OrgCtx,
    db: ScopedDB,
    user_id: str,
) -> GenerateResponse:
    """generate_message 的实际执行逻辑（拆分以支持 try/except 槽位释放）"""

    # 1.5+2. 解析生成类型与请求位置上下文
    gen_type = await resolve_generation_context(request, body)
    requested_turn_id = str(uuid.uuid4())

    # 3+4. 权限校验、图片积分预检和用户消息创建
    handler = get_handler(
        gen_type, db, org_id=ctx.org_id, user_id=user_id,
        request_id=request.headers.get("X-Request-Id", ""),
    )
    handler, conversation, user_message = await prepare_generation_request(
        db=db, conversation_id=conversation_id, body=body, gen_type=gen_type,
        user_id=user_id, org_id=ctx.org_id, handler=handler,
        conversation_service=get_conversation_service(db),
        create_user_message_fn=create_user_message,
        turn_id=requested_turn_id,
    )

    if body.params is None:
        body.params = {}
    body.params["_prefetched_summary"] = conversation.get("context_summary")
    body.params["_org_id"] = ctx.org_id

    # 5. 处理助手消息（根据操作类型）
    assistant_message_id, assistant_message = await prepare_assistant_message(
        db, conversation_id, body, gen_type,
    )

    if user_message:
        input_message_id, turn_id = user_message.id, requested_turn_id
    else:
        input_message_id, turn_id = resolve_existing_turn_anchor(
            db, conversation_id, assistant_message_id,
        )
    if user_message:
        user_message.turn_id = turn_id
    assistant_message.turn_id = turn_id
    assistant_message.reply_to_message_id = input_message_id

    # 5.1 记录用户反馈信号（retry/regenerate = 用户对生成结果的隐式反馈）
    if body.operation in (
        MessageOperation.RETRY,
        MessageOperation.REGENERATE,
        MessageOperation.REGENERATE_SINGLE,
    ):
        _record_user_feedback_signal(
            db=db,
            user_id=user_id,
            operation=body.operation.value,
            model=body.model,
            gen_type=gen_type.value,
            original_message_id=body.original_message_id,
            conversation_id=conversation_id,
        )

    # 6. 启动生成任务
    try:
        external_task_id = await start_generation_task(
            db=db,
            handler=handler,
            assistant_message_id=assistant_message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            content=body.content,
            model=body.model,
            params=body.params,
            client_task_id=body.client_task_id,
            placeholder_created_at=body.placeholder_created_at,
            operation=body.operation,
            input_message_id=input_message_id,
            turn_id=turn_id,
        )
    except AppException as exc:
        if gen_type == GenerationType.IMAGE and exc.code == "IMAGE_GENERATION_FAILED":
            finalize_image_request_failure(
                db=db,
                message_id=assistant_message_id,
                operation=body.operation,
                params=body.params,
                error_code=exc.code,
                error_message=exc.message,
            )
        raise
    record_user_activity(
        db,
        user_id=user_id,
        event_type="task_created",
        org_id=ctx.org_id,
        source="web",
        resource_type="task",
        resource_id=external_task_id,
        metadata={
            "conversation_id": conversation_id,
            "generation_type": gen_type.value,
            "operation": body.operation.value,
        },
    )

    # 7. 确定返回的 client_task_id（Handler 已保存到数据库）
    client_task_id = body.client_task_id or external_task_id

    # 8. 返回结果
    return GenerateResponse(
        task_id=client_task_id,
        user_message=user_message,
        assistant_message=assistant_message,
        operation=body.operation,
        generation_type=gen_type.value,
    )


# ============================================================
# 消息 CRUD API
# ============================================================


@router.get("", response_model=MessageListResult, summary="获取消息列表")
async def get_messages(
    conversation_id: str,
    ctx: OrgCtx,
    limit: int = Query(default=100, ge=1, le=1000, description="每页数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    before_id: Optional[str] = Query(default=None, description="获取此消息之前的消息"),
    service: MessageService = Depends(get_message_service),
):
    """
    获取对话的消息列表

    按创建时间降序返回（从新到旧），支持分页加载历史消息。
    """
    result = await service.get_messages(
        conversation_id=conversation_id,
        user_id=ctx.user_id,
        limit=limit,
        offset=offset,
        before_id=before_id,
        org_id=ctx.org_id,
    )
    return result


@router.get("/search", response_model=MessageSearchResult, summary="搜索消息")
async def search_messages(
    conversation_id: str,
    ctx: OrgCtx,
    q: str = Query(..., min_length=1, max_length=200, description="搜索关键词"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量上限"),
    service: MessageService = Depends(get_message_service),
):
    """
    在指定对话内全文搜索消息

    实现：JSONB content 字段 ILIKE 模糊匹配，按时间倒序返回最近的匹配消息。
    用于"翻不到的远期消息"场景，配合前端 SearchPanel 组件使用。

    注意：路由必须放在 /{message_id} 之前，否则会被贪婪 path 参数吃掉。
    """
    result = await service.search_messages(
        conversation_id=conversation_id,
        user_id=ctx.user_id,
        query=q,
        limit=limit,
        org_id=ctx.org_id,
    )
    return result


@router.get("/{message_id}", response_model=MessageResponse, summary="获取消息详情")
async def get_message(
    conversation_id: str,
    message_id: str,
    ctx: OrgCtx,
    service: MessageService = Depends(get_message_service),
):
    """获取单条消息的详细信息"""
    result = await service.get_message(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )
    return result


# ==================== 独立消息路由 ====================


@message_router.delete("/{message_id}", response_model=DeleteMessageResponse, summary="删除消息")
async def delete_message(
    message_id: str,
    ctx: OrgCtx,
    service: MessageService = Depends(get_message_service),
):
    """
    删除单条消息

    权限验证：只能删除自己对话中的消息
    """
    result = await service.delete_message(
        message_id=message_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
    )
    return result
