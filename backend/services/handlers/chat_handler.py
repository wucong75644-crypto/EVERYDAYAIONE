"""
聊天消息处理器

处理流式聊天消息生成。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from schemas.message import (
    ContentPart,
    GenerationType,
    Message,
    MessageStatus,
    TextPart,
)
from schemas.websocket import (
    build_chat_start_message,
    build_chat_chunk_message,
    build_chat_done_message,
    build_chat_error_message,
)
from services.handlers.base import BaseHandler
from services.websocket_manager import ws_manager


class ChatHandler(BaseHandler):
    """
    聊天消息处理器

    特点：
    - 流式生成
    - 实时推送 WebSocket
    - 支持多模态输入（VQA）
    """

    def __init__(self, db: Client):
        super().__init__(db)
        self._adapter = None  # 当前使用的适配器

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
    ) -> str:
        """
        启动聊天任务

        1. 生成 task_id
        2. 保存任务到数据库
        3. 更新消息状态为 streaming
        4. 启动异步流式生成
        """
        # 1. 生成 task_id
        task_id = str(uuid.uuid4())

        # 2. 获取模型配置
        model_id = params.get("model") or "gemini-3-flash"
        thinking_effort = params.get("thinking_effort")
        thinking_mode = params.get("thinking_mode")

        # 3. 保存任务到数据库
        await self._save_task(
            task_id=task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_id=model_id,
            content=content,
            params=params,
        )

        # 4. 更新消息状态为 streaming
        await self._update_message(message_id, status=MessageStatus.STREAMING)

        # 5. 启动异步流式生成
        asyncio.create_task(
            self._stream_generate(
                task_id=task_id,
                message_id=message_id,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                model_id=model_id,
                thinking_effort=thinking_effort,
                thinking_mode=thinking_mode,
            )
        )

        logger.info(
            f"Chat task started | task_id={task_id} | "
            f"message_id={message_id} | model={model_id}"
        )

        return task_id

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
    ) -> None:
        """流式生成主逻辑"""
        accumulated_text = ""
        final_usage = {"prompt_tokens": 0, "completion_tokens": 0}

        try:
            # 1. 推送开始消息
            start_msg = build_chat_start_message(
                task_id=task_id,
                conversation_id=conversation_id,
                model=model_id,
                assistant_message_id=message_id,
            )
            await ws_manager.send_to_task_subscribers(task_id, start_msg)

            # 2. 准备消息列表（转换为 LLM 格式）
            text_content = self._extract_text_content(content)
            image_url = self._extract_image_url(content)

            messages = [{"role": "user", "content": text_content}]
            if image_url:
                # 添加图片到消息（VQA 模式）
                messages[0]["content"] = [
                    {"type": "text", "text": text_content},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]

            # 3. 创建适配器
            from services.adapters.factory import create_chat_adapter

            self._adapter = create_chat_adapter(model_id)

            # 4. 流式生成（直接使用适配器）
            async for chunk in self._adapter.stream_chat(
                messages=messages,
                reasoning_effort=thinking_effort,
                thinking_mode=thinking_mode,  # 直接传递 'deep_think' 或 None
            ):
                if chunk.content:
                    accumulated_text += chunk.content

                    # 推送增量内容
                    chunk_msg = build_chat_chunk_message(
                        task_id=task_id,
                        text=chunk.content,
                        conversation_id=conversation_id,
                        accumulated=accumulated_text,
                    )
                    await ws_manager.send_to_task_subscribers(task_id, chunk_msg)

                # 捕获 usage
                if chunk.prompt_tokens or chunk.completion_tokens:
                    final_usage["prompt_tokens"] = chunk.prompt_tokens or 0
                    final_usage["completion_tokens"] = chunk.completion_tokens or 0

            # 5. 计算积分消耗（使用统一入口）
            from config.kie_models import calculate_chat_cost

            cost_result = calculate_chat_cost(
                model_name=model_id,
                input_tokens=final_usage["prompt_tokens"],
                output_tokens=final_usage["completion_tokens"],
            )
            credits_consumed = max(1, cost_result["user_credits"])  # 最少 1 积分

            # 6. 完成回调
            await self.on_complete(
                task_id=task_id,
                result=[TextPart(text=accumulated_text)],
                credits_consumed=credits_consumed,
            )

        except Exception as e:
            logger.error(f"Chat stream error | task_id={task_id} | error={str(e)}")
            await self.on_error(
                task_id=task_id,
                error_code="GENERATION_FAILED",
                error_message=str(e),
            )

        finally:
            if self._adapter:
                await self._adapter.close()

    async def on_complete(
        self,
        task_id: str,
        result: List[ContentPart],
        credits_consumed: int = 0,
    ) -> Message:
        """完成回调"""
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]
        user_id = task["user_id"]
        model_id = task.get("model_id", "gemini-3-flash")

        # 1. 扣除积分（Chat 完成后直接扣除）
        if credits_consumed > 0:
            await self._deduct_directly(
                user_id=user_id,
                amount=credits_consumed,
                reason=f"Chat: {model_id}",
                change_type="chat_generation",
            )

        # 2. 转换 ContentPart 为字典
        content_dicts = []
        for part in result:
            if isinstance(part, TextPart):
                content_dicts.append({"type": "text", "text": part.text})
            elif isinstance(part, dict):
                content_dicts.append(part)

        # 3. 更新消息
        message = await self._update_message(
            message_id=message_id,
            content=content_dicts,
            status=MessageStatus.COMPLETED,
            credits_cost=credits_consumed,
        )

        # 4. 推送完成消息（使用旧格式保持兼容）
        text_content = self._extract_text_content(result)
        done_msg = build_chat_done_message(
            task_id=task_id,
            conversation_id=conversation_id,
            message_id=message_id,
            content=text_content,
            credits_consumed=credits_consumed,
            model=model_id,
        )
        await ws_manager.send_to_task_subscribers(task_id, done_msg, buffer=False)

        # 5. 更新任务状态
        await self._complete_task(task_id)

        logger.info(
            f"Chat completed | task_id={task_id} | "
            f"message_id={message_id} | credits={credits_consumed}"
        )

        return message

    async def on_error(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> Message:
        """错误回调"""
        task = await self._get_task(task_id)
        if not task:
            logger.error(f"Task not found | task_id={task_id}")
            raise Exception("任务不存在")

        message_id = task["placeholder_message_id"]
        conversation_id = task["conversation_id"]

        # 1. 更新消息为失败状态
        message = await self._update_message(
            message_id=message_id,
            content=[{"type": "text", "text": error_message}],
            status=MessageStatus.FAILED,
            error={"code": error_code, "message": error_message},
        )

        # 2. 推送错误消息
        error_msg = build_chat_error_message(
            task_id=task_id,
            error=error_message,
            conversation_id=conversation_id,
            error_code=error_code,
        )
        await ws_manager.send_to_task_subscribers(task_id, error_msg, buffer=False)

        # 3. 更新任务状态
        await self._fail_task(task_id, error_message)

        logger.error(
            f"Chat failed | task_id={task_id} | "
            f"error_code={error_code} | error={error_message}"
        )

        return message

    async def _save_task(
        self,
        task_id: str,
        message_id: str,
        conversation_id: str,
        user_id: str,
        model_id: str,
        content: List[ContentPart],
        params: Dict[str, Any],
    ) -> None:
        """保存任务到数据库"""
        # 构建请求参数
        request_params = {
            "content": self._extract_text_content(content),
            "model_id": model_id,
            **{k: v for k, v in params.items() if v is not None},
        }

        self.db.table("tasks").insert({
            "external_task_id": task_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "type": "chat",
            "status": "running",
            "model_id": model_id,
            "placeholder_message_id": message_id,
            "request_params": request_params,
        }).execute()

