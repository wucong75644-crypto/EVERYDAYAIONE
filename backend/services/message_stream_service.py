"""
流式消息服务

处理消息的流式发送和重新生成等业务逻辑。
"""

import json
from typing import Optional, AsyncIterator, TYPE_CHECKING

from loguru import logger
from supabase import Client

from core.config import get_settings
from services.adapters.kie.client import KieAPIError
from services.message_utils import format_message, deduct_user_credits
from services.message_ai_helpers import prepare_ai_stream_client, stream_ai_response

if TYPE_CHECKING:
    from services.message_service import MessageService
    from services.conversation_service import ConversationService


class MessageStreamService:
    """流式消息服务类"""

    def __init__(
        self,
        db: Client,
        message_service: "MessageService",
        conversation_service: "ConversationService",
    ) -> None:
        """
        初始化流式消息服务

        Args:
            db: Supabase 客户端
            message_service: 消息服务实例（用于调用 create_message 等方法）
            conversation_service: 对话服务实例
        """
        self.db = db
        self.message_service = message_service
        self.conversation_service = conversation_service

    async def send_message_stream(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        model_id: Optional[str] = None,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        thinking_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        client_request_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """
        流式发送消息并获取 AI 响应

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            content: 消息内容
            model_id: 模型 ID
            image_url: 图片 URL（可选，用于 VQA）
            video_url: 视频 URL（可选，用于视频 QA）
            thinking_effort: 推理强度（可选，Gemini 3 专用）
            thinking_mode: 推理模式（可选，Gemini 3 Pro Deep Think）
            client_request_id: 客户端请求ID（可选，用于乐观更新）

        Yields:
            SSE 格式的流式数据
        """
        # 1. 创建用户消息并发送事件
        user_message = await self.message_service.create_message(
            conversation_id, user_id, content, "user", 0, image_url, video_url,
            client_request_id=client_request_id
        )
        yield f"data: {json.dumps({'type': 'user_message', 'data': {'user_message': user_message}})}\n\n"
        # 2. 更新对话标题（如果需要）
        await self.message_service._update_conversation_title_if_first_message(
            conversation_id, user_id, content
        )
        # 3. 检查AI服务配置
        settings = get_settings()
        if not settings.kie_api_key:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'AI 服务未配置'}})}\n\n"
            yield "data: [DONE]\n\n"
            return
        # 4. 准备AI客户端
        model, client, adapter = prepare_ai_stream_client(model_id)
        full_content = ""
        total_credits = 0
        try:
            yield f"data: {json.dumps({'type': 'start', 'data': {'model': model}})}\n\n"
            # 5. 流式获取AI响应
            stream = await stream_ai_response(
                adapter=adapter,
                get_conversation_history_func=self.message_service._get_conversation_history,
                conversation_id=conversation_id,
                user_id=user_id,
                content=content,
                image_url=image_url,
                video_url=video_url,
                thinking_effort=thinking_effort,
                thinking_mode=thinking_mode,
            )
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                        yield f"data: {json.dumps({'type': 'content', 'data': {'text': delta.content}})}\n\n"
                if chunk.usage:
                    cost = adapter.estimate_cost(
                        chunk.usage.prompt_tokens,
                        chunk.usage.completion_tokens,
                    )
                    total_credits = cost.estimated_credits
            # 6. 保存消息并扣除积分
            if full_content:
                assistant_message = await self.message_service.create_message(
                    conversation_id, user_id, full_content, "assistant", total_credits
                )
                await deduct_user_credits(
                    self.db, user_id, total_credits, f"AI 对话 ({model})"
                )
                yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': assistant_message, 'credits_consumed': total_credits}})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': None, 'credits_consumed': 0}})}\n\n"
        except KieAPIError as e:
            logger.error(
                f"AI stream failed | conversation_id={conversation_id} | "
                f"user_id={user_id} | error={e.message}"
            )
            async for event in self._handle_stream_error(
                conversation_id, user_id, "抱歉，AI 服务暂时不可用，请稍后重试。"
            ):
                yield event
        except Exception as e:
            logger.error(
                f"Unexpected error in AI stream | conversation_id={conversation_id} | "
                f"user_id={user_id} | error={e}"
            )
            async for event in self._handle_stream_error(
                conversation_id, user_id, "发生了意外错误，请稍后重试。"
            ):
                yield event
        finally:
            await client.close()
            yield "data: [DONE]\n\n"

    async def regenerate_message_stream(
        self,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> AsyncIterator[str]:
        """
        重新生成失败的消息（流式）

        Args:
            conversation_id: 对话 ID
            message_id: 要重新生成的消息 ID
            user_id: 用户 ID

        Yields:
            SSE 流式事件
        """
        # 1. 权限校验
        validation_result = await self._validate_regenerate_permission(
            conversation_id, message_id, user_id
        )
        if validation_result["error"]:
            yield validation_result["error"]
            yield "data: [DONE]\n\n"
            return
        message = validation_result["message"]
        # 2. 检查AI服务配置
        settings = get_settings()
        if not settings.kie_api_key:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'AI 服务未配置'}})}\n\n"
            yield "data: [DONE]\n\n"
            return
        # 3. 准备AI客户端
        conversation = await self.conversation_service.get_conversation(conversation_id, user_id)
        model_id = conversation.get("model_id")
        model, client, adapter = prepare_ai_stream_client(model_id)
        full_content = ""
        total_credits = 0
        try:
            yield f"data: {json.dumps({'type': 'start', 'data': {'model': model}})}\n\n"
            # 4. 获取上下文用户消息
            context_result = await self._get_last_user_message(conversation_id, message)
            if context_result["error"]:
                yield context_result["error"]
                yield "data: [DONE]\n\n"
                return
            last_user_content = context_result["content"]
            # 5. 流式获取AI响应
            stream = await stream_ai_response(
                adapter=adapter,
                get_conversation_history_func=self.message_service._get_conversation_history,
                conversation_id=conversation_id,
                user_id=user_id,
                content=last_user_content,
                image_url=None,
                video_url=None,
                thinking_effort=None,
                thinking_mode=None,
            )
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_content += delta.content
                        yield f"data: {json.dumps({'type': 'content', 'data': {'text': delta.content}})}\n\n"
                if chunk.usage:
                    cost = adapter.estimate_cost(
                        chunk.usage.prompt_tokens,
                        chunk.usage.completion_tokens,
                    )
                    total_credits = cost.estimated_credits
            # 6. 更新消息并扣除积分
            if full_content:
                self.db.table("messages").update({
                    "content": full_content,
                    "is_error": False,
                    "credits_cost": total_credits,
                }).eq("id", message_id).execute()
                await deduct_user_credits(
                    self.db, user_id, total_credits, f"AI 对话重新生成 ({model})"
                )
                await self.conversation_service.update_last_message_preview(
                    conversation_id, full_content
                )
                updated_message_result = self.db.table("messages").select("*").eq("id", message_id).single().execute()
                updated_message = format_message(updated_message_result.data)
                yield f"data: {json.dumps({'type': 'done', 'data': {'assistant_message': updated_message, 'credits_consumed': total_credits}})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': 'AI 未返回内容'}})}\n\n"
        except KieAPIError as e:
            logger.error(
                f"AI regenerate failed | message_id={message_id} | error={e.message}"
            )
            async for event in self._handle_stream_error(
                conversation_id, user_id, "重试失败，AI 服务暂时不可用。"
            ):
                yield event
        except Exception as e:
            logger.error(
                f"Unexpected error in regenerate | message_id={message_id} | error={e}"
            )
            async for event in self._handle_stream_error(
                conversation_id, user_id, "重试时发生意外错误。"
            ):
                yield event
        finally:
            await client.close()
            yield "data: [DONE]\n\n"

    async def _validate_regenerate_permission(
        self,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> dict:
        """
        验证消息重新生成权限

        Args:
            conversation_id: 对话 ID
            message_id: 消息 ID
            user_id: 用户 ID

        Returns:
            dict: {"message": 消息数据或None, "error": 错误SSE事件或None}
        """
        message_result = self.db.table("messages").select("*").eq("id", message_id).single().execute()
        if not message_result.data:
            error_event = f"data: {json.dumps({'type': 'error', 'data': {'message': '消息不存在'}})}\n\n"
            return {"message": None, "error": error_event}

        message = message_result.data

        if message["conversation_id"] != conversation_id:
            error_event = f"data: {json.dumps({'type': 'error', 'data': {'message': '消息不属于此对话'}})}\n\n"
            return {"message": None, "error": error_event}

        try:
            await self.conversation_service.get_conversation(conversation_id, user_id)
        except Exception:
            error_event = f"data: {json.dumps({'type': 'error', 'data': {'message': '无权访问此对话'}})}\n\n"
            return {"message": None, "error": error_event}

        return {"message": message, "error": None}

    async def _get_last_user_message(
        self,
        conversation_id: str,
        message: dict,
    ) -> dict:
        """
        获取错误消息之前的最后一条用户消息

        Args:
            conversation_id: 对话 ID
            message: 当前错误消息数据

        Returns:
            dict: {"content": 用户消息内容或None, "error": 错误SSE事件或None}
        """
        history_result = self.db.table("messages") \
            .select("role,content") \
            .eq("conversation_id", conversation_id) \
            .lt("created_at", message["created_at"]) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        last_user_content = None
        for msg in (history_result.data or []):
            if msg["role"] == "user":
                last_user_content = msg["content"]
                break

        if not last_user_content:
            error_event = f"data: {json.dumps({'type': 'error', 'data': {'message': '未找到对应的用户消息'}})}\n\n"
            return {"content": None, "error": error_event}

        return {"content": last_user_content, "error": None}

    async def _handle_stream_error(
        self,
        conversation_id: str,
        user_id: str,
        error_content: str,
    ) -> AsyncIterator[str]:
        """
        处理流式响应错误

        Args:
            conversation_id: 对话 ID
            user_id: 用户 ID
            error_content: 错误消息内容

        Yields:
            错误SSE事件
        """
        error_message = await self.message_service.create_error_message(
            conversation_id=conversation_id,
            user_id=user_id,
            content=error_content,
        )
        yield f"data: {json.dumps({'type': 'error', 'data': {'message': error_content, 'error_message': error_message}})}\n\n"
