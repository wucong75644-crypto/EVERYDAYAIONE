"""
图片生成处理器

处理图片生成任务（异步模式）。
统一路径：单图（num_images=1）当作 batch_size=1 的批次处理。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


from schemas.message import (
    ContentPart,
    GenerationType,
    ImagePart,
    Message,
    serialize_content_part,
)
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.image_request_settings import (
    build_image_generate_kwargs,
    resolve_image_generation_settings,
    resolve_batch_item_kwargs,
)


class ImageHandler(BaseHandler):
    """
    图片生成处理器

    特点：
    - 异步任务模式，统一批次路径（1~4 张）
    - 支持文生图和图生图
    - 通过 WebSocket 推送完成状态
    """

    def __init__(self, db):
        super().__init__(db)

    async def accept_chat_image(self, owner: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        """Persist one frozen child. No prepare, provider IO or credit debit.

        `owner` is the Policy-approved executor, never a model argument. Actor
        fencing comes from the active handler and is checked again by the RPC.
        """
        from core.config import get_settings
        from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
        from services.tools.dispatcher import current_dispatch_call_id
        from services.handlers.chat_image_request import ChatImageInputResolver, freeze_image_request, chat_image_acceptance_allowed

        settings=get_settings()
        if not chat_image_acceptance_allowed(settings, getattr(owner, "user_id", None)):
            raise PermissionError("CHAT_IMAGE_ASYNC_DISABLED")
        call_id=current_dispatch_call_id()
        token=getattr(owner,"image_execution_token",None)
        if (not call_id or not token or not owner.task_id or owner.context_scope != "user"
                or owner.execution_mode != "interactive" or owner.workspace_user_id != owner.user_id):
            raise PermissionError("CHAT_IMAGE_TRUSTED_CONTEXT_REQUIRED")
        parent = await asyncio.to_thread(lambda: owner.db.table("tasks").select(
            "id,user_id,org_id,conversation_id,turn_id,input_message_id,base_context_revision"
        ).eq("id",owner.task_id).single().execute().data)
        if (not parent or parent.get("user_id") != owner.user_id or parent.get("org_id") != owner.org_id
                or parent.get("conversation_id") != owner.conversation_id
                or parent.get("base_context_revision") is None):
            raise PermissionError("CHAT_IMAGE_PARENT_DENIED")
        resolver=ChatImageInputResolver(owner,base_revision=parent["base_context_revision"],
            input_message_id=str(parent["input_message_id"]))
        normalized=await asyncio.to_thread(resolver.normalize_legacy,args)
        if normalized.get("background")=="transparent" and not settings.chat_image_transparent_enabled:
            raise PermissionError("CHAT_IMAGE_TRANSPARENT_DISABLED")
        refs=await asyncio.to_thread(resolver.resolve,normalized.get("references",[]))
        if "source_prompt" in normalized:
            normalized["source_prompt"] = await asyncio.to_thread(resolver.source_prompt,normalized["source_prompt"],normalized["prompt"])
        if "source_task_id" in normalized:
            await asyncio.to_thread(resolver.validate_source_task,normalized["source_task_id"])
        origin={"parent_task_id":owner.task_id,"tool_call_id":call_id,
            "actor_user_id":owner.user_id,"workspace_owner_id":owner.workspace_user_id,
            "org_id":owner.org_id,"context_scope":owner.context_scope,
            "conversation_id":owner.conversation_id,"turn_id":str(parent["turn_id"]),
            "input_message_id":str(parent["input_message_id"]),
            "base_context_revision":parent["base_context_revision"]}
        origin["skills"] = list(getattr(owner, "image_skill_snapshot", ()))
        snapshot=freeze_image_request(normalized,refs,origin=origin,
            max_requests=settings.chat_image_max_requests,max_credits=settings.chat_image_max_credits)
        await asyncio.to_thread(resolver.verify,refs)
        if owner.cancellation_event is not None and owner.cancellation_event.is_set():
            raise asyncio.CancelledError()
        scoped=ScopedDatabaseClient(self.db,DatabaseScope(owner.user_id,owner.org_id,DatabaseAccessKind.RUNTIME))
        result=await asyncio.to_thread(lambda:scoped.rpc("accept_chat_image_request",{
            "p_parent_task_id":owner.task_id,"p_execution_token":token,
            "p_snapshot":snapshot,"p_org_id":owner.org_id,
        }).execute().data)
        if not isinstance(result,dict) or result.get("outcome") not in {"accepted","replay"}:
            raise RuntimeError("CHAT_IMAGE_ACCEPTANCE_NOT_CONFIRMED")
        try:
            from schemas.websocket import build_media_pending
            from services.websocket_manager import ws_manager
            import json
            message=await asyncio.to_thread(lambda:scoped.table("messages").select("*").eq("id",result["message_id"]).single().execute().data)
            if isinstance(message.get("content"),str):
                message["content"]=json.loads(message["content"])
            await ws_manager.send_to_task_or_user(result["task_id"],owner.user_id,
                build_media_pending(result["task_id"],owner.conversation_id,message,result["submission_state"]),org_id=owner.org_id)
        except Exception as error:
            logger.warning("Chat image accepted; pending snapshot delivery deferred | task={} | error_type={}",result["task_id"],type(error).__name__)
        return {"status":"submitted",**result,"mode":snapshot["mode"],
            "model":snapshot["model"],"aspect_ratio":snapshot["aspect_ratio"],
            "resolution":snapshot["resolution"],"estimated_credits":snapshot["estimated_credits"]}

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.IMAGE

    async def accept_image_trial(self, *, actor_id, org_id, trial_id, conversation_id,
                                 args, trial_facts, result):
        """Same snapshot/worker path, isolated trial destination, no chat stub."""
        from types import SimpleNamespace
        from core.config import get_settings
        from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
        from services.handlers.chat_image_request import ChatImageInputResolver, freeze_image_request, chat_image_acceptance_allowed
        settings=get_settings()
        if not chat_image_acceptance_allowed(settings, actor_id):
            raise PermissionError("CHAT_IMAGE_ASYNC_DISABLED")
        if args.get("background")=="transparent" and not settings.chat_image_transparent_enabled:
            raise PermissionError("CHAT_IMAGE_TRANSPARENT_DISABLED")
        scoped=ScopedDatabaseClient(self.db,DatabaseScope(actor_id,org_id,DatabaseAccessKind.RUNTIME_ADMIN))
        conv=await asyncio.to_thread(lambda:scoped.table("conversations").select("user_id,org_id,scope_type,context_revision").eq("id",conversation_id).single().execute().data)
        if conv.get("user_id")!=actor_id or conv.get("org_id")!=org_id or conv.get("scope_type","user")!="user":
            raise PermissionError("CHAT_IMAGE_TRIAL_CONVERSATION_DENIED")
        owner=SimpleNamespace(db=scoped,user_id=actor_id,workspace_user_id=actor_id,org_id=org_id,
            conversation_id=conversation_id,context_scope="user",resource_manifest=None,execution_mode="interactive")
        resolver=ChatImageInputResolver(owner,base_revision=conv["context_revision"],input_message_id="")
        refs=await asyncio.to_thread(resolver.resolve,args.get("references",[]))
        origin={"destination":"skill_trial","trial_id":trial_id,"actor_user_id":actor_id,
            "workspace_owner_id":actor_id,"org_id":org_id,"context_scope":"user",
            "conversation_id":conversation_id,"base_context_revision":conv["context_revision"],"input_message_id":"",
            **{key:trial_facts[key] for key in ("change_set_id","candidate_revision","content_sha256","audit_subject_sha256") if key in trial_facts}}
        frozen=freeze_image_request(args,refs,origin=origin,max_requests=1,max_credits=settings.chat_image_max_credits)
        await asyncio.to_thread(resolver.verify,refs)
        accepted=await asyncio.to_thread(lambda:scoped.rpc("accept_chat_image_trial",{
            "p_trial_id":trial_id,"p_snapshot":frozen,"p_result":result,"p_org_id":org_id,
        }).execute().data)
        return {**result,"image_task_id":accepted["task_id"],"status":"running",
            "submission_state":accepted["submission_state"]}

    def preflight(
        self,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
    ) -> None:
        """在消息占位符变更前校验本次图片请求的积分。"""
        settings = resolve_image_generation_settings(
            params=params,
            has_image_urls=bool(self._extract_image_urls(content)),
        )
        self._check_balance(user_id, settings["total_credits"])

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> str:
        """
        启动图片生成任务（统一批次路径）

        num_images=1 也走批次逻辑（batch_size=1），不做 if/else 分流。

        流程：
        1. 提取 prompt 和参考图
        2. 计算总积分并校验余额
        3. 循环创建 N 个本地任务（绑定 Turn → 锁积分 → API 调用）
        4. 返回 client_task_id
        """
        # 1. 提取参数
        prompt = self._extract_text_content(content)
        image_urls = self._extract_image_urls(content)
        if self.handler_type == GenerationType.IMAGE:
            from services.skills.media import prepare_media_prompt
            prompt = await prepare_media_prompt(
                self, conversation_id=conversation_id, user_id=user_id,
                params=params, metadata=metadata,
                task_mode=params.get('_skill_task_mode') or ('image-i2i' if image_urls else 'image-t2i'),
                prompt=prompt, image_urls=image_urls,
            )
        settings = resolve_image_generation_settings(
            params=params,
            has_image_urls=bool(image_urls),
        )
        model_id = settings["model_id"]
        aspect_ratio = settings["aspect_ratio"]
        output_format = params.get("output_format") or "png"
        if params.get("taobao_main_image") is True:
            params = {**params, "taobao_main_image": aspect_ratio == "1:1"}

        # regenerate_single：仅生成 1 张，使用指定 image_index
        is_regenerate_single = params.get("operation") == "regenerate_single"
        # Agent Loop 批量生图：每张图有独立提示词
        batch_prompts = params.get("_batch_prompts")
        num_images = settings["num_images"]
        if is_regenerate_single:
            single_image_index = int(params.get("image_index", 0))

        # 2. 计算总积分并校验余额
        total_credits = settings["total_credits"]
        per_image_credits = total_credits // num_images

        self._check_balance(user_id, total_credits)

        logger.info(
            f"Image batch start | client_task_id={metadata.client_task_id} | "
            f"message_id={message_id} | model={model_id} | "
            f"num_images={num_images} | total_credits={total_credits}"
        )

        # 3. 统一批次逻辑
        batch_id = str(uuid.uuid4())
        tasks_created: List[str] = []

        from services.adapters.factory import create_image_adapter

        adapter = create_image_adapter(
            model_id, shadow_user_id=user_id, shadow_org_id=self.org_id,
        )

        # 构建生成参数（所有图片共用）
        generate_kwargs = build_image_generate_kwargs(
            prompt=prompt,
            image_urls=image_urls,
            settings=settings,
            output_format=output_format,
            callback_url=self._build_callback_url(adapter.provider.value),
            supports_resolution=adapter.supports_resolution,
        )

        try:
            for i in range(num_images):
                if i > 0:
                    await asyncio.sleep(0.3)  # 300ms 间隔，尊重 KIE 频率限制

                # regenerate_single 使用指定的 image_index，否则使用循环 index
                actual_index = single_image_index if is_regenerate_single else i

                # Agent Loop / Ecom 批量生图：每张图可覆盖 prompt/aspect_ratio/image_urls/resolution
                task_kwargs = generate_kwargs
                task_prompt = prompt
                batch_index = single_image_index if is_regenerate_single else i
                if batch_prompts and batch_index < len(batch_prompts):
                    task_kwargs, task_prompt = resolve_batch_item_kwargs(
                        generate_kwargs, prompt, aspect_ratio, batch_prompts[batch_index],
                    )

                ext_task_id = await self._create_single_task(
                    adapter=adapter,
                    index=actual_index,
                    batch_id=batch_id,
                    generate_kwargs=task_kwargs,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    model_id=model_id,
                    per_image_credits=per_image_credits,
                    params=({
                        **params,
                        "aspect_ratio": task_kwargs["size"],
                        "resolution": task_kwargs.get("resolution"),
                        "taobao_main_image": task_kwargs["size"] == "1:1",
                    } if params.get("taobao_main_image") is True else params),
                    prompt=task_prompt,
                    metadata=metadata,
                )
                if ext_task_id:
                    tasks_created.append(ext_task_id)
        finally:
            await adapter.close()

        if not tasks_created:
            from core.exceptions import AppException
            raise AppException(
                code="IMAGE_GENERATION_FAILED",
                message="图片生成服务暂时不可用，请稍后重试",
                status_code=502,
            )

        logger.info(
            f"Image batch created | batch_id={batch_id} | "
            f"created={len(tasks_created)}/{num_images} | "
            f"client_task_id={metadata.client_task_id}"
        )

        return metadata.client_task_id or tasks_created[0]

    async def _create_single_task(
        self,
        adapter: Any,
        index: int,
        batch_id: str,
        generate_kwargs: Dict[str, Any],
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        per_image_credits: int,
        params: Dict[str, Any],
        prompt: str,
        metadata: TaskMetadata,
    ) -> Optional[str]:
        """
        创建单个图片生成任务（创建并绑定本地 task → 锁积分 → API）

        Returns:
            external_task_id 或 None（失败时）
        """
        local_task_id = str(uuid.uuid4())
        transaction_id = self._lock_credits(
            task_id=local_task_id,
            user_id=user_id,
            amount=per_image_credits,
            reason=f"Image[{index}]: {model_id}",
            org_id=self.org_id,
        )

        try:
            self._save_task(
                task_id=local_task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                model_id=model_id,
                prompt=prompt,
                params=params,
                metadata=metadata,
                credits_locked=per_image_credits,
                transaction_id=transaction_id,
                image_index=index,
                batch_id=batch_id,
                local_task_id=local_task_id,
                defer_external_task_id=True,
            )
        except Exception as save_err:
            logger.critical(
                f"Image task pre-save failed, refunding | local_task_id={local_task_id} | "
                f"batch_id={batch_id} | transaction_id={transaction_id} | "
                f"error={save_err}"
            )
            try:
                self._refund_credits(transaction_id)
            except Exception as refund_err:
                logger.critical(
                    f"Image pre-save refund also failed | tx={transaction_id} | "
                    f"error={refund_err}"
                )
            return None

        try:
            result = await adapter.generate(**generate_kwargs)
            external_task_id = result.task_id
        except Exception as e:
            try:
                self._refund_credits(transaction_id)
            except Exception as refund_err:
                logger.critical(f"Image refund also failed | tx={transaction_id} | error={refund_err}")
            logger.warning(
                f"Image task[{index}] API failed | "
                f"batch_id={batch_id} | error={e}"
            )

            # Smart mode: 尝试用替代模型重试
            try:
                retry_result = await self._attempt_image_sync_retry(
                    prompt=prompt, model_id=model_id, error=str(e),
                    params=params, generate_kwargs=generate_kwargs,
                    user_id=user_id, per_image_credits=per_image_credits,
                    index=index, batch_id=batch_id, message_id=message_id,
                    conversation_id=conversation_id, metadata=metadata,
                    local_task_id=local_task_id,
                )
            except Exception as retry_err:
                logger.warning(
                    f"Image sync retry setup failed | local_task_id={local_task_id} | "
                    f"error={retry_err}"
                )
                retry_result = None
            if retry_result:
                return retry_result
            self._update_task_by_id(
                local_task_id,
                {
                    "status": "failed",
                    "error_message": str(e),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return None

        try:
            self._update_task_by_id(
                local_task_id,
                {
                    "external_task_id": external_task_id,
                    "error_message": None,
                },
            )
        except Exception as update_err:
            logger.critical(
                f"Image external task binding failed, refunding | "
                f"local_task_id={local_task_id} | external_task_id={external_task_id} | "
                f"batch_id={batch_id} | transaction_id={transaction_id} | "
                f"error={update_err}"
            )
            try:
                self._refund_credits(transaction_id)
            except Exception as refund_err:
                logger.critical(
                    f"Image external task binding refund also failed | "
                    f"tx={transaction_id} | error={refund_err}"
                )
            self._update_task_by_id(
                local_task_id,
                {
                    "status": "failed",
                    "error_message": str(update_err),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return None

        return external_task_id

    async def _attempt_image_sync_retry(
        self,
        prompt: str,
        model_id: str,
        error: str,
        params: Dict[str, Any],
        generate_kwargs: Dict[str, Any],
        user_id: str,
        per_image_credits: int,
        index: int,
        batch_id: str,
        message_id: str,
        conversation_id: str,
        metadata: TaskMetadata,
        local_task_id: str,
    ) -> Optional[str]:
        """Smart mode 同步重试：API 调用失败时尝试替代模型"""
        if not params.get("_is_smart_mode"):
            return None

        from services.intent_router import RetryContext

        ctx = RetryContext(
            is_smart_mode=True,
            original_content=prompt,
            generation_type=GenerationType.IMAGE,
        )
        ctx.add_failure(model_id, error)

        while ctx.can_retry:
            decision = await self._route_retry(ctx)
            if not decision or not decision.recommended_model:
                break

            new_model = decision.recommended_model
            attempt = len(ctx.failed_attempts)
            logger.info(
                f"Image sync retry | index={index} | attempt={attempt} | "
                f"{model_id} → {new_model}"
            )

            from services.adapters.factory import create_image_adapter

            new_adapter = create_image_adapter(
                new_model, shadow_user_id=user_id, shadow_org_id=self.org_id,
            )
            new_tx = self._lock_credits(
                task_id=local_task_id,
                user_id=user_id,
                amount=per_image_credits,
                reason=f"Image[{index}] retry: {new_model}",
                org_id=self.org_id,
            )

            try:
                new_kwargs = {**generate_kwargs}
                new_kwargs["callback_url"] = self._build_callback_url(
                    new_adapter.provider.value
                )
                result = await new_adapter.generate(**new_kwargs)
            except Exception as retry_err:
                try:
                    self._refund_credits(new_tx)
                except Exception as refund_err:
                    logger.critical(f"Image retry refund failed | tx={new_tx} | error={refund_err}")
                ctx.add_failure(new_model, str(retry_err))
                logger.warning(
                    f"Image sync retry failed | index={index} | "
                    f"model={new_model} | error={retry_err}"
                )
                continue
            finally:
                await new_adapter.close()

            # API 成功 → 更新同一条本地 task。重试不创建第二条批次记录。
            try:
                retry_params = {
                    **params,
                    "_retried": True,
                    "_retry_from_model": model_id,
                }
                self._update_task_by_id(
                    local_task_id,
                    {
                        "external_task_id": result.task_id,
                        "model_id": new_model,
                        "status": "pending",
                        "request_params": {
                            "prompt": prompt,
                            "model": new_model,
                            **self._serialize_params(retry_params),
                        },
                        "credits_locked": per_image_credits,
                        "credit_transaction_id": new_tx,
                        "error_message": None,
                        "completed_at": None,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as save_err:
                logger.critical(
                    f"Image retry task update failed, refunding | index={index} | "
                    f"local_task_id={local_task_id} | "
                    f"external_task_id={result.task_id} | tx={new_tx} | error={save_err}"
                )
                try:
                    self._refund_credits(new_tx)
                except Exception as refund_err:
                    logger.critical(
                        f"Image retry _save_task refund also failed | tx={new_tx} | "
                        f"error={refund_err}"
                    )
                return None
            return result.task_id

        return None

    # ========================================
    # 基类抽象方法实现
    # ========================================

    def _convert_content_parts_to_dicts(self, result: List[ContentPart]) -> List[Dict[str, Any]]:
        """转换 ImagePart 为字典"""
        content_dicts = []
        for part in result:
            if isinstance(part, ImagePart):
                content_dicts.append(serialize_content_part(part))
            elif isinstance(part, dict):
                content_dicts.append(part)
        return content_dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Image 完成时确认积分扣除"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._confirm_deduct(transaction_id)
        return task.get("credits_locked", credits_consumed)

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        """Image 错误时退回积分"""
        transaction_id = task.get("credit_transaction_id")
        if transaction_id:
            self._refund_credits(transaction_id)

    # ========================================
    # 回调方法（调用基类通用流程）
    # ========================================

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调（调用基类通用流程）"""
        image_count = sum(1 for p in result if isinstance(p, ImagePart))
        if image_count != 1:
            logger.warning(
                f"IMAGE_COUNT_MISMATCH | task_id={task_id} | "
                f"expected=1 | actual={image_count} | credits_consumed={credits_consumed}"
            )
        return await self._handle_complete_common(task_id, result, credits_consumed)

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）"""
        return await self._handle_error_common(task_id, error_code, error_message)

    def _save_task(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        prompt: str,
        params: Dict[str, Any],
        metadata: TaskMetadata,
        credits_locked: int = 0,
        transaction_id: Optional[str] = None,
        image_index: Optional[int] = None,
        batch_id: Optional[str] = None,
        local_task_id: Optional[str] = None,
        defer_external_task_id: bool = False,
    ) -> None:
        """保存任务到数据库"""
        request_params = {
            "prompt": prompt,
            "model": model_id,
            **self._serialize_params(params),
        }

        task_data = self._build_task_data(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            task_type="image",
            status="pending",
            model_id=model_id,
            request_params=request_params,
            metadata=metadata,
            credits_locked=credits_locked,
            transaction_id=transaction_id,
            image_index=image_index,
            batch_id=batch_id,
            local_task_id=local_task_id,
            defer_external_task_id=defer_external_task_id,
        )

        if defer_external_task_id:
            # 任务在 Provider 调用前已进入 pending，便于超时清理发现提交中断。
            task_data["started_at"] = datetime.now(timezone.utc).isoformat()

        self._insert_task_with_turn_binding(task_data, metadata)

        logger.info(
            f"Task saved | local_task_id={task_data['id']} | "
            f"external_task_id={task_data.get('external_task_id')} | "
            f"client_task_id={metadata.client_task_id} | "
            f"image_index={image_index} | batch_id={batch_id}"
        )
