"""
企业微信统一消息处理服务

接收企微消息（来自长连接或回调）→ 映射用户 → 管理对话 →
Agent Loop 路由 → AI 生成 → 流式回复到企微。

两个渠道共用此服务，仅回复方式不同。
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client

from core.config import get_settings
from schemas.message import ContentPart, GenerationType, TextPart
from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.conversation_service import ConversationService
from services.wecom.user_mapping_service import WecomUserMappingService
from services.wecom.wecom_ai_mixin import WecomAIMixin


class WecomMessageService(WecomAIMixin):
    """企微消息处理核心：用户映射 → 对话管理 → Agent Loop 路由 → AI 生成 → 回复"""

    def __init__(self, db: Client):
        self.db = db
        self.settings = get_settings()
        self._user_svc = WecomUserMappingService(db)
        self._conv_svc = ConversationService(db)

    async def handle_message(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """处理企微消息的完整流程。"""
        start_time = time.monotonic()

        try:
            # 1. 用户映射
            user_id = await self._user_svc.get_or_create_user(
                wecom_userid=msg.wecom_userid,
                corp_id=msg.corp_id,
                channel=msg.channel,
            )

            # 2. 获取或创建对话
            conversation_id = await self._get_or_create_conversation(
                user_id=user_id,
                chatid=msg.chatid,
                chattype=msg.chattype,
            )

            # 3. 保存用户消息到 DB
            await self._save_user_message(
                conversation_id=conversation_id,
                user_id=user_id,
                text_content=msg.text_content or "",
            )

            # 4. 创建 assistant 占位消息
            assistant_message_id = await self._create_assistant_placeholder(
                conversation_id=conversation_id,
            )

            # 5. 根据消息类型处理
            if msg.msgtype in (WecomMsgType.TEXT, WecomMsgType.VOICE):
                await self._handle_text(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_message_id,
                    text_content=msg.text_content or "",
                    reply_ctx=reply_ctx,
                )
            else:
                await self._reply_text(
                    reply_ctx, "暂时只支持文本消息哦，发文字给我试试~"
                )

            elapsed = int((time.monotonic() - start_time) * 1000)
            logger.info(
                f"Wecom message handled | msgid={msg.msgid} | "
                f"user_id={user_id} | elapsed={elapsed}ms"
            )

        except Exception as e:
            logger.error(
                f"Wecom message handling failed | msgid={msg.msgid} | "
                f"error={e}"
            )
            await self._reply_text(reply_ctx, "抱歉，处理消息时出了点问题，请稍后再试。")

    # ── AI 路由 + 分发 ──────────────────────────────────

    async def _handle_text(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """文本消息处理：Agent Loop 路由 → 按类型分发"""
        try:
            content_parts: List[ContentPart] = [TextPart(text=text_content)]

            # 并行：Agent Loop + 记忆预取
            agent_raw, memory_raw = await asyncio.gather(
                self._run_agent_loop(user_id, conversation_id, content_parts),
                self._build_memory_prompt(user_id, text_content),
                return_exceptions=True,
            )

            # Agent Loop 失败 → 兜底纯文本聊天
            if isinstance(agent_raw, BaseException):
                logger.warning(f"Wecom routing failed | error={agent_raw}")
                await self._handle_chat_fallback(
                    user_id, conversation_id, message_id,
                    text_content, reply_ctx,
                )
                return

            agent_result = agent_raw
            memory_prompt = (
                memory_raw if not isinstance(memory_raw, BaseException) else None
            )
            gen_type = agent_result.generation_type

            if gen_type == GenerationType.CHAT:
                await self._handle_chat_response(
                    user_id, conversation_id, message_id,
                    text_content, reply_ctx, agent_result, memory_prompt,
                )
            elif gen_type == GenerationType.IMAGE:
                await self._handle_image_response(
                    user_id, conversation_id, message_id,
                    text_content, reply_ctx, agent_result,
                )
            elif gen_type == GenerationType.VIDEO:
                await self._handle_video_response(
                    user_id, conversation_id, message_id,
                    text_content, reply_ctx, agent_result,
                )
            else:
                await self._handle_chat_fallback(
                    user_id, conversation_id, message_id,
                    text_content, reply_ctx,
                )

        except Exception as e:
            logger.error(f"Wecom _handle_text failed | user_id={user_id} | error={e}")
            await self._reply_text(reply_ctx, "生成回复时遇到了问题，请稍后再试。")

    # ── 对话管理 ──────────────────────────────────────────

    async def _get_or_create_conversation(
        self,
        user_id: str,
        chatid: str,
        chattype: str,
    ) -> str:
        """获取或创建企微对话。按 user_id 查找最近的企微对话。"""
        try:
            result = (
                self.db.table("conversations")
                .select("id")
                .eq("user_id", user_id)
                .like("title", "企微%")
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )

            if result.data:
                return result.data[0]["id"]

            title = "企微群聊" if chattype == WecomChatType.GROUP else "企微对话"
            conv = await self._conv_svc.create_conversation(
                user_id=user_id,
                title=title,
                model_id="auto",
            )
            return conv["id"]

        except Exception as e:
            logger.error(f"Conversation get/create failed | user_id={user_id} | error={e}")
            raise

    async def _get_conversation_history(
        self,
        conversation_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """获取对话历史（role + content 格式）"""
        try:
            result = (
                self.db.table("messages")
                .select("role, content")
                .eq("conversation_id", conversation_id)
                .neq("status", "failed")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

            if not result.data:
                return []

            messages = []
            for row in reversed(result.data):
                content = row.get("content")
                role = row.get("role", "user")
                text = self._extract_text_from_content(content)
                if text:
                    messages.append({"role": role, "content": text})

            return messages

        except Exception as e:
            logger.warning(f"Get conversation history failed | error={e}")
            return []

    # ── 消息持久化 ────────────────────────────────────────

    async def _save_user_message(
        self,
        conversation_id: str,
        user_id: str,
        text_content: str,
    ) -> str:
        """保存用户消息到 DB"""
        msg_data = {
            "conversation_id": conversation_id,
            "role": "user",
            "content": [{"type": "text", "text": text_content}],
            "status": "completed",
        }
        result = self.db.table("messages").insert(msg_data).execute()
        msg_id = result.data[0]["id"]

        self.db.rpc("increment_message_count", {
            "conv_id": conversation_id,
        }).execute()

        return msg_id

    async def _create_assistant_placeholder(
        self,
        conversation_id: str,
    ) -> str:
        """创建 assistant 占位消息"""
        msg_data = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "status": "generating",
        }
        result = self.db.table("messages").insert(msg_data).execute()
        return result.data[0]["id"]

    async def _update_assistant_message(
        self,
        message_id: str,
        text: str,
    ) -> None:
        """更新 assistant 消息为完成状态"""
        self.db.table("messages").update({
            "content": [{"type": "text", "text": text}],
            "status": "completed",
        }).eq("id", message_id).execute()

    # ── 回复发送 ──────────────────────────────────────────

    async def _push_stream_chunk(
        self,
        reply_ctx: WecomReplyContext,
        stream_id: str,
        content: str,
        finish: bool,
    ) -> None:
        """推送流式 chunk 到企微"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            await reply_ctx.ws_client.send_stream_chunk(
                req_id=reply_ctx.req_id,
                stream_id=stream_id,
                content=content,
                finish=finish,
            )
        elif reply_ctx.channel == "app" and finish:
            await self._send_app_message(reply_ctx, content)

    async def _reply_text(
        self, reply_ctx: WecomReplyContext, text: str
    ) -> None:
        """发送文本回复（app 通道自动适配 markdown_v2）"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            await reply_ctx.ws_client.send_reply(
                req_id=reply_ctx.req_id,
                msgtype="text",
                content={"content": text},
            )
        elif reply_ctx.channel == "app":
            await self._send_app_message(reply_ctx, text)

    async def _send_app_message(
        self, reply_ctx: WecomReplyContext, text: str
    ) -> None:
        """自建应用消息发送：格式适配 + 长消息分割"""
        import asyncio
        from services.wecom.app_message_sender import send_text, send_markdown_v2
        from services.wecom.markdown_adapter import adapt_for_app, split_long_message

        adapted, msgtype = adapt_for_app(text)
        chunks = split_long_message(adapted, max_bytes=2000)

        for i, chunk in enumerate(chunks):
            if msgtype == "markdown_v2":
                await send_markdown_v2(
                    wecom_userid=reply_ctx.wecom_userid,
                    content=chunk,
                    agent_id=reply_ctx.agent_id,
                )
            else:
                await send_text(
                    wecom_userid=reply_ctx.wecom_userid,
                    content=chunk,
                    agent_id=reply_ctx.agent_id,
                )
            # 多条消息间延迟，避免乱序
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)

    # ── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _extract_text_from_content(content: Any) -> Optional[str]:
        """从 content 字段提取文本"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return part.get("text", "")
        return None
