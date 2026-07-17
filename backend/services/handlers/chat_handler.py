"""
聊天消息处理器

处理流式聊天消息生成 + 工具循环执行。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger


from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
)
from services.adapters.factory import DEFAULT_MODEL_ID
from services.handlers.base import BaseHandler, TaskMetadata
from services.handlers.chat_context_mixin import ChatContextMixin
from services.handlers.chat_generate_mixin import ChatGenerateMixin
from services.handlers.chat_stream_support_mixin import ChatStreamSupportMixin
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.websocket_manager import ws_manager


class ChatHandler(ChatGenerateMixin, ChatToolMixin, ChatStreamSupportMixin, ChatContextMixin, BaseHandler):
    """聊天消息处理器：流式生成 + WebSocket 推送 + 多模态输入"""

    def __init__(self, db):
        super().__init__(db)
        self._adapter = None
        # 沙盒 IO 统一协议:emit_chart/file/image/table 产物统一收集器
        # 每项 dict 必含 kind: "chart"|"file"|"image"|"table"
        # file/image 含 url(CDN) + workspace_path(本地相对路径) 双轨字段
        self._pending_emit_payloads: list = []

    @property
    def handler_type(self) -> GenerationType:
        return GenerationType.CHAT

    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
        metadata: TaskMetadata,
    ) -> str:
        """启动聊天任务：生成 task_id → 保存到 DB → 启动异步流式生成"""
        # 1. 获取或生成 task_id（优先使用前端提供的 client_task_id）
        task_id = metadata.client_task_id or str(uuid.uuid4())

        # 2. 获取模型配置
        model_id = params.get("model") or DEFAULT_MODEL_ID

        from core.config import get_settings

        if get_settings().conversation_actor_web_enabled:
            from services.handlers.chat.actor_enqueue import enqueue_web_chat

            return await enqueue_web_chat(
                handler=self,
                external_task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                model_id=model_id,
                content=content,
                params=params,
                metadata=metadata,
            )

        # 3. 保存任务到数据库
        context_anchor = self._save_task(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_id=model_id,
            content=content,
            params=params,
            metadata=metadata,
        )

        # 4. 启动流式生成（单循环工具编排，不再有异步路由分支）
        asyncio.create_task(
            self._stream_generate(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                model_id=model_id,
                thinking_effort=params.get("thinking_effort"),
                thinking_mode=params.get("thinking_mode"),
                permission_mode=params.get("permission_mode", "auto"),
                needs_google_search=params.get("_needs_google_search", False),
                _params=params,
                context_anchor=context_anchor,
            )
        )

        logger.info(
            f"Chat task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id}"
        )

        return task_id


    async def _save_accumulated_content(self, task_id: str, content: str) -> None:
        """将累积内容写入数据库（供刷新恢复使用）"""
        try:
            self.db.table("tasks").update(
                {"accumulated_content": content}
            ).eq("external_task_id", task_id).execute()
        except Exception as e:
            logger.warning(f"Failed to save accumulated_content | task_id={task_id} | error={e}")

    async def _save_accumulated_blocks(self, task_id: str, blocks: List[Dict[str, Any]]) -> None:
        """将结构化内容块写入数据库（供刷新恢复 thinking + tool_step 等）"""
        try:
            self.db.table("tasks").update(
                {"accumulated_blocks": blocks}
            ).eq("external_task_id", task_id).execute()
        except Exception as e:
            logger.warning(f"Failed to save accumulated_blocks | task_id={task_id} | error={e}")

    async def _handle_user_cancel(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        content_blocks: List[Dict[str, Any]],
        location: str,
        partial_text: str = "",
        partial_thinking: str = "",
    ) -> None:
        """用户取消统一收尾：沙盒 interrupt + 落锚原子操作 + 日志。

        详见 docs/document/TECH_用户中断与恢复机制.md §四.2 / §M3
        """
        from services.handlers.interrupt_anchor import persist_interrupt_anchor
        logger.info(f"Task cancelled by user ({location}) | task={task_id}")

        # 给沙盒 kernel 发 SIGINT（业界标准 Jupyter interrupt 模式）
        # 中断当前执行的代码但保留 kernel + 变量，下次 code_execute 立即可用
        try:
            from services.sandbox.kernel_manager import get_kernel_manager
            km = get_kernel_manager()
            if km is not None:
                km.interrupt(conversation_id)
        except Exception as e:
            logger.warning(f"Kernel interrupt failed | task={task_id} | error={e}")

        await persist_interrupt_anchor(
            db=self.db,
            task_id=task_id,
            message_id=message_id,
            org_id=self.org_id,
            messages=messages,
            content_blocks=content_blocks,
            partial_text=partial_text,
            partial_thinking=partial_thinking,
        )

    async def _stream_generate(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[ContentPart],
        model_id: str,
        thinking_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        permission_mode: str = "auto",
        needs_google_search: bool = False,
        _params: Optional[Dict[str, Any]] = None,
        _retry_context: Optional[Any] = None,
        context_anchor: Optional[Any] = None,
    ) -> None:
        """流式生成主逻辑（支持工具循环 + smart_mode 自动重试）"""
        from services.handlers.chat.stream_runner import (
            LegacyStreamRequest,
            run_legacy_chat_stream,
        )

        await run_legacy_chat_stream(
            handler=self,
            request=LegacyStreamRequest(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                model_id=model_id,
                thinking_effort=thinking_effort,
                thinking_mode=thinking_mode,
                permission_mode=permission_mode,
                needs_google_search=needs_google_search,
                params=_params,
                retry_context=_retry_context,
                context_anchor=context_anchor,
            ),
            websocket=ws_manager,
        )

    def _convert_content_parts_to_dicts(self, result):
        """转换 ContentPart 为字典

        所有 Pydantic BaseModel 子类统一用 model_dump()，
        不再逐类型手写字段映射——新增类型不会被静默跳过。
        """
        from pydantic import BaseModel
        dicts = []
        for p in result:
            if isinstance(p, BaseModel):
                dicts.append(p.model_dump(exclude_none=True))
            elif isinstance(p, dict):
                dicts.append(p)
        return dicts

    async def _handle_credits_on_complete(
        self,
        task: Dict[str, Any],
        credits_consumed: int,
    ) -> int:
        """Chat 完成时直接扣除积分"""
        if credits_consumed > 0:
            user_id = task["user_id"]
            model_id = task.get("model_id", DEFAULT_MODEL_ID)
            self._deduct_directly(
                user_id=user_id,
                amount=credits_consumed,
                reason=f"Chat: {model_id}",
                change_type="conversation_cost",
                org_id=self.org_id,
            )
        return credits_consumed

    async def _handle_credits_on_error(self, task: Dict[str, Any]) -> None:
        pass  # Chat 无预扣，无需退回

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
        tool_digest: Optional[dict] = None,
    ) -> Message:
        """完成回调（调用基类通用流程）"""
        return await self._handle_complete_common(
            task_id, result, credits_consumed,
            tool_digest=tool_digest,
        )

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调（调用基类通用流程）"""
        return await self._handle_error_common(task_id, error_code, error_message)

    def _save_task(
        self, task_id, message_id, conversation_id, user_id,
        model_id, content, params, metadata,
    ):
        """保存任务到数据库"""
        request_params = {
            "content": self._extract_text_content(content),
            "model_id": model_id,
            **self._serialize_params(params),
        }
        task_data = self._build_task_data(
            task_id=task_id, message_id=message_id,
            conversation_id=conversation_id, user_id=user_id,
            task_type="chat", status="running", model_id=model_id,
            request_params=request_params, metadata=metadata,
        )
        return self._insert_task_with_turn_binding(task_data, metadata)


async def _async_cleanup_staging(
    conversation_id: str,
    user_id: str = "",
    org_id: str | None = None,
) -> None:
    """NAS 替代后不再需要 staging 清理（保留签名兼容测试）"""
    pass
