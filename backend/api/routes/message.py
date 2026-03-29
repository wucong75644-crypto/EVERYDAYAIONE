"""
统一消息路由

提供统一的消息生成入口 /messages/generate。
支持聊天、图片、视频等多种生成类型。
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from loguru import logger

from api.deps import CurrentUser, CurrentUserId, Database, OrgCtx, TaskLimitSvc
from core.limiter import limiter, RATE_LIMITS
from schemas.message import (
    DeleteMessageResponse,
    GenerateRequest,
    GenerateResponse,
    GenerationType,
    Message,
    MessageListResult,
    MessageOperation,
    MessageResponse,
    infer_generation_type,
)
from services.message_service import MessageService
from services.conversation_service import ConversationService
from services.handlers import get_handler

from api.routes.message_generation_helpers import (
    handle_retry_operation,
    handle_regenerate_or_send_operation,
    handle_regenerate_single_operation,
    start_generation_task,
    create_user_message,
)

# 向后兼容别名：test_placeholder_to_db.py 使用了带下划线前缀的名称
_handle_retry_operation = handle_retry_operation
_handle_regenerate_or_send_operation = handle_regenerate_or_send_operation
_start_generation_task = start_generation_task
_create_user_message = create_user_message

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["消息"])
message_router = APIRouter(prefix="/messages", tags=["消息"])


def get_message_service(db: Database) -> MessageService:
    """获取消息服务实例"""
    return MessageService(db)


def get_conversation_service(db: Database) -> ConversationService:
    """获取对话服务实例"""
    return ConversationService(db)


# ============================================================
# 智能路由
# ============================================================


async def _resolve_generation_type(body, user_id: str, conversation_id: str, db=None, org_id: str | None = None):
    """推断生成类型：Agent Loop → IntentRouter 降级 → 关键词兜底"""
    from loguru import logger
    from services.intent_router import SMART_MODEL_ID

    # 非智能模式 / retry / regenerate_single → 不走 Agent Loop
    if body.model != SMART_MODEL_ID and body.generation_type:
        return body.generation_type, None
    if body.operation in (MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE):
        return infer_generation_type(body.content), None

    # Agent Loop（开关控制，可回退到 IntentRouter）
    from core.config import get_settings
    if get_settings().agent_loop_enabled and db is not None:
        from services.agent_loop import AgentLoop

        agent = AgentLoop(db, user_id, conversation_id, org_id=org_id)
        try:
            thinking_mode = (body.params or {}).get("thinking_mode")
            result = await agent.run(body.content, thinking_mode=thinking_mode)
            logger.info(
                f"Agent loop completed | type={result.generation_type.value} | "
                f"turns={result.turns_used} | tokens={result.total_tokens} | "
                f"user_id={user_id}"
            )
            return result.generation_type, result
        except Exception as e:
            logger.warning(
                f"Agent loop failed, legacy fallback | "
                f"type={type(e).__name__} | error={e!r}"
            )
        finally:
            await agent.close()

    # 降级到旧路由（IntentRouter 单步路由）
    return await _legacy_resolve(body, user_id, conversation_id, org_id=org_id)


async def _legacy_resolve(body, user_id: str, conversation_id: str, org_id: str | None = None):
    """旧路由降级路径（IntentRouter 单步路由）"""
    from services.intent_router import IntentRouter

    router = IntentRouter()
    try:
        decision = await router.route(
            content=body.content, user_id=user_id, conversation_id=conversation_id,
            org_id=org_id,
        )

        # web_search：执行搜索，将结果作为上下文注入
        if decision.raw_tool_name == "web_search" and decision.search_query:
            user_text = " ".join(p.text for p in body.content if hasattr(p, "text"))
            search_result = await router.execute_search(
                query=decision.search_query,
                user_text=user_text,
                system_prompt=decision.system_prompt,
            )
            if search_result:
                decision.tool_params["_search_context"] = search_result

        return decision.generation_type, decision
    except Exception:
        return infer_generation_type(body.content), None
    finally:
        await router.close()


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
                except Exception:
                    pass

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
    db: Database,
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

    # 1. 检查任务限制
    if task_limit_service:
        await task_limit_service.check_and_acquire(user_id, conversation_id, org_id=ctx.org_id)

    # 1.5 异步获取用户 IP 位置（与路由并行，不阻塞主流程）
    from services.ip_location_service import extract_client_ip, get_location_by_ip

    client_ip = extract_client_ip(request)
    location_task = asyncio.create_task(get_location_by_ip(client_ip))

    # 2. 推断生成类型（智能路由 / 关键词兜底）
    from services.intent_router import SMART_MODEL_ID, resolve_auto_model

    # Smart mode + send → 异步路由：跳过 Agent Loop，ChatHandler 中异步执行
    _is_smart_deferred = (
        body.model == SMART_MODEL_ID
        and body.operation == MessageOperation.SEND
    )

    if _is_smart_deferred:
        # 使用 provisional chat 类型，路由在 ChatHandler 异步阶段完成
        gen_type = GenerationType.CHAT
        routing_decision = None
        if body.params is None:
            body.params = {}
        body.params["_is_smart_mode"] = True
        body.params["_needs_routing"] = True
        # provisional 模型（ChatHandler 路由完成后替换为实际模型）
        body.model = resolve_auto_model(gen_type, body.content, None)
    else:
        # 非智能模式 / retry / regenerate → 同步路由（原有逻辑）
        gen_type, routing_decision = await _resolve_generation_type(
            body, user_id, conversation_id, db=db, org_id=ctx.org_id,
        )

        # 智能模型：标记 smart_mode + 解析实际工作模型
        if body.model == SMART_MODEL_ID:
            if body.params is None:
                body.params = {}
            body.params["_is_smart_mode"] = True

            from services.agent_loop import AgentResult
            if isinstance(routing_decision, AgentResult):
                recommended = routing_decision.model or None
                if recommended:
                    body.model = recommended
                else:
                    body.model = resolve_auto_model(gen_type, body.content, None)
            else:
                recommended = routing_decision.recommended_model if routing_decision else None
                body.model = resolve_auto_model(gen_type, body.content, recommended)

        # 将路由结果注入 params（供 handler 使用）
        if routing_decision:
            if body.params is None:
                body.params = {}

            if isinstance(routing_decision, AgentResult):
                if routing_decision.system_prompt:
                    body.params["_router_system_prompt"] = routing_decision.system_prompt
                if routing_decision.search_context:
                    body.params["_router_search_context"] = routing_decision.search_context
                if routing_decision.direct_reply:
                    body.params["_direct_reply"] = routing_decision.direct_reply
                if routing_decision.batch_prompts:
                    body.params["_batch_prompts"] = routing_decision.batch_prompts
                    body.params["num_images"] = len(routing_decision.batch_prompts)
                    first_ratio = routing_decision.batch_prompts[0].get("aspect_ratio", "1:1")
                    if "aspect_ratio" not in body.params:
                        body.params["aspect_ratio"] = first_ratio
                if routing_decision.render_hints:
                    body.params["_render"] = routing_decision.render_hints
                if routing_decision.tool_params.get("_needs_google_search"):
                    body.params["_needs_google_search"] = True
                for key in ("prompt", "resolution", "aspect_ratio", "output_format"):
                    val = routing_decision.tool_params.get(key)
                    if val is not None:
                        body.params[key] = val
            else:
                if routing_decision.system_prompt:
                    body.params["_router_system_prompt"] = routing_decision.system_prompt
                if routing_decision.tool_params.get("_search_context"):
                    body.params["_router_search_context"] = routing_decision.tool_params["_search_context"]
                for key in ("resolution", "aspect_ratio", "output_format"):
                    val = routing_decision.tool_params.get(key)
                    if val is not None:
                        body.params[key] = val

        # 智能模式下图片参数兜底
        if gen_type == GenerationType.IMAGE and body.params:
            if body.params.get("_is_smart_mode") and "aspect_ratio" not in body.params:
                body.params["aspect_ratio"] = "1:1"

    # 2.5 等待 IP 位置结果并注入 params（与路由并行完成，此处仅 await 结果）
    try:
        user_location = await location_task
    except Exception:
        user_location = None
    if user_location:
        if body.params is None:
            body.params = {}
        body.params["_user_location"] = user_location

    # 3+4. 验证对话权限 + 创建用户消息（并行执行，两者独立无数据依赖）
    conversation_service = get_conversation_service(db)
    user_message: Optional[Message] = None
    needs_user_message = body.operation not in (
        MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE,
    )

    if needs_user_message:
        conv_result, msg_result = await asyncio.gather(
            conversation_service.get_conversation(conversation_id, user_id, ctx.org_id),
            create_user_message(
                db=db,
                conversation_id=conversation_id,
                content=body.content,
                created_at=body.created_at,
                client_request_id=body.client_request_id,
            ),
        )
        conversation = conv_result
        user_message = msg_result
    else:
        conversation = await conversation_service.get_conversation(
            conversation_id, user_id, ctx.org_id,
        )

    if body.params is None:
        body.params = {}
    body.params["_prefetched_summary"] = conversation.get("context_summary")
    body.params["_org_id"] = ctx.org_id

    # 5. 处理助手消息（根据操作类型）
    if body.operation == MessageOperation.RETRY:
        assistant_message_id, assistant_message = await handle_retry_operation(
            db=db,
            conversation_id=conversation_id,
            original_message_id=body.original_message_id,
            gen_type=gen_type,
            model=body.model,
            params=body.params,
        )
    elif body.operation == MessageOperation.REGENERATE_SINGLE:
        assistant_message_id, assistant_message = await handle_regenerate_single_operation(
            db=db,
            conversation_id=conversation_id,
            original_message_id=body.original_message_id,
            params=body.params,
        )
    else:
        assistant_message_id, assistant_message = await handle_regenerate_or_send_operation(
            db=db,
            conversation_id=conversation_id,
            operation=body.operation,
            original_message_id=body.original_message_id,
            assistant_message_id=body.assistant_message_id,
            placeholder_created_at=body.placeholder_created_at,
            gen_type=gen_type,
            model=body.model,
            params=body.params,
        )

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

    # 6. 获取 Handler 并启动任务
    handler = get_handler(gen_type, db)
    handler.org_id = ctx.org_id

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
