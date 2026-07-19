"""
聊天消息处理器

处理流式聊天消息生成 + 工具循环执行。
"""

import uuid
from typing import Any, Dict, List, Optional

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
