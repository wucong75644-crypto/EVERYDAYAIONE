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


from core.config import get_settings
from schemas.message import ContentPart, GenerationType, TextPart
from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.conversation_service import ConversationService
from services.websocket_manager import ws_manager
from services.wecom.user_mapping_service import WecomUserMappingService
from services.wecom.wecom_ai_mixin import WecomAIMixin


class WecomMessageService(WecomAIMixin):
    """企微消息处理核心：用户映射 → 对话管理 → Agent Loop 路由 → AI 生成 → 回复"""

    # 企微会话级设置缓存（内存级，进程重启后重置）
    # key = conversation_id, value = {"model": "...", "thinking_mode": "..."}
    _session_settings: Dict[str, Dict[str, str]] = {}

    def __init__(self, db):
        self.db = db
        self.settings = get_settings()
        self._user_svc = WecomUserMappingService(db)
        self._conv_svc = ConversationService(db)

    @classmethod
    def get_session_setting(cls, conv_id: str, key: str) -> Optional[str]:
        """获取企微会话级设置"""
        return cls._session_settings.get(conv_id, {}).get(key)

    @classmethod
    def set_session_setting(cls, conv_id: str, key: str, value: str) -> None:
        """设置企微会话级设置"""
        if conv_id not in cls._session_settings:
            cls._session_settings[conv_id] = {}
        cls._session_settings[conv_id][key] = value

    async def handle_message(
        self,
        msg: WecomIncomingMessage,
        reply_ctx: WecomReplyContext,
    ) -> None:
        """处理企微消息的完整流程。"""
        start_time = time.monotonic()

        try:
            org_id = msg.org_id

            # 1. 用户映射
            user_id = await self._user_svc.get_or_create_user(
                wecom_userid=msg.wecom_userid,
                corp_id=msg.corp_id,
                channel=msg.channel,
                org_id=org_id,
            )

            # 1.5 更新 chatid（主动推送用）
            await self._user_svc.update_last_chatid(
                msg.wecom_userid, msg.corp_id, msg.chatid, msg.chattype,
            )

            # 1.6 记录聊天目标（定时任务推送目标选择用）
            await self._user_svc.upsert_chat_target(
                msg.chatid, msg.chattype, msg.corp_id, org_id=org_id,
            )

            # 2. 获取或创建对话
            conversation_id = await self._get_or_create_conversation(
                user_id=user_id,
                chatid=msg.chatid,
                chattype=msg.chattype,
                org_id=org_id,
            )

            # 2.5 指令拦截：匹配成功则直接回复卡片，跳过 AI 路由
            if msg.text_content and msg.msgtype in (
                WecomMsgType.TEXT, WecomMsgType.VOICE,
            ):
                from services.wecom.command_handler import CommandHandler
                cmd = CommandHandler(self.db)
                if await cmd.try_handle(
                    msg.text_content, user_id, conversation_id, reply_ctx,
                    org_id=org_id,
                ):
                    return

            # 3. 多模态资源下载（图片 URL → OSS 永久 URL，需在保存前完成）
            oss_image_urls = await self._download_media(msg, user_id)

            # 4. 保存用户消息到 DB（含图片 OSS URL）
            await self._save_user_message(
                conversation_id=conversation_id,
                user_id=user_id,
                text_content=msg.text_content or "",
                image_urls=oss_image_urls,
            )

            # 4.5 通知 Web 前端对话列表有更新
            await self._notify_web_conversation_updated(
                user_id, conversation_id,
            )

            # 5. 创建 assistant 占位消息
            assistant_message_id = await self._create_assistant_placeholder(
                conversation_id=conversation_id,
            )

            # 6. 根据消息类型处理
            if msg.msgtype in (
                WecomMsgType.TEXT, WecomMsgType.VOICE,
                WecomMsgType.IMAGE, WecomMsgType.MIXED,
            ):
                await self._handle_text(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_message_id,
                    text_content=msg.text_content or "",
                    reply_ctx=reply_ctx,
                    image_urls=oss_image_urls,
                    org_id=org_id,
                )
            elif msg.msgtype in (WecomMsgType.FILE, WecomMsgType.VIDEO):
                # 文件/视频暂不支持 AI 分析，提示用户
                await self._reply_text(
                    reply_ctx,
                    "收到你的文件，目前暂不支持文件内容分析，发文字或图片给我试试~",
                )
            else:
                await self._reply_text(
                    reply_ctx, "暂时不支持这种消息类型，发文字或图片给我试试~"
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

    # ── 多媒体下载 ──────────────────────────────────────

    async def _download_media(
        self,
        msg: WecomIncomingMessage,
        user_id: str,
    ) -> List[str]:
        """下载企微图片到 OSS，返回 OSS URL 列表。

        图片 URL 有 5 分钟有效期，必须在路由前下载。
        """
        if not msg.image_urls:
            return []

        from services.wecom.media_downloader import WecomMediaDownloader
        downloader = WecomMediaDownloader()

        oss_urls = []
        for url in msg.image_urls:
            aeskey = msg.aeskeys.get(url)
            oss_url = await downloader.download_and_store(
                url=url,
                user_id=user_id,
                aeskey=aeskey,
                media_type="image",
            )
            if oss_url:
                oss_urls.append(oss_url)
            else:
                logger.warning(f"Wecom image download failed | url={url[:80]}")

        return oss_urls

    # ── AI 路由 + 分发 ──────────────────────────────────

    async def _handle_text(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        text_content: str,
        reply_ctx: WecomReplyContext,
        image_urls: Optional[List[str]] = None,
        org_id: Optional[str] = None,
    ) -> None:
        """文本/多模态消息处理：Agent Loop 路由 → 按类型分发"""
        from schemas.message import ImagePart
        from services.wecom.stream_keepalive import StreamKeepAlive

        keepalive: StreamKeepAlive | None = None
        try:
            # 立即发送占位 + 启动保活（每 3 秒更新进度，防止 req_id 失效）
            if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
                import uuid
                stream_id = str(uuid.uuid4())
                reply_ctx.active_stream_id = stream_id
                await self._push_stream_chunk(
                    reply_ctx, stream_id, "🤔 思考中", finish=False,
                )
                keepalive = StreamKeepAlive(reply_ctx, self._push_stream_chunk)
                await keepalive.start()

            # 构建多模态 content_parts
            content_parts: List[ContentPart] = []
            if text_content:
                content_parts.append(TextPart(text=text_content))
            for url in (image_urls or []):
                content_parts.append(ImagePart(url=url))
            if not content_parts:
                content_parts.append(TextPart(text="（用户发送了一张图片）"))

            # 并行：Agent Loop + 记忆预取（保活在后台持续运行）
            agent_raw, memory_raw = await asyncio.gather(
                self._run_agent_loop(user_id, conversation_id, content_parts, org_id=org_id),
                self._build_memory_prompt(user_id, text_content, org_id=org_id),
                return_exceptions=True,
            )

            # 停止保活（即将发送真实内容）
            if keepalive:
                await keepalive.stop()
                keepalive = None

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
                    image_urls=image_urls,
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
        finally:
            if keepalive:
                await keepalive.stop()

    # ── 对话管理 ──────────────────────────────────────────

    async def _get_or_create_conversation(
        self,
        user_id: str,
        chatid: str,
        chattype: str,
        org_id: Optional[str] = None,
    ) -> str:
        """获取或创建企微对话。按 user_id + org_id 查找最近的企微对话。"""
        try:
            query = (
                self.db.table("conversations")
                .select("id")
                .eq("user_id", user_id)
                .like("title", "企微%")
            )
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")
            result = (
                query
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
                org_id=org_id,
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
        image_urls: Optional[List[str]] = None,
    ) -> str:
        """保存用户消息到 DB（支持多模态内容）"""
        content: List[Dict[str, str]] = []
        if text_content:
            content.append({"type": "text", "text": text_content})
        for url in (image_urls or []):
            content.append({"type": "image", "url": url})
        if not content:
            content.append({"type": "text", "text": ""})

        msg_data = {
            "conversation_id": conversation_id,
            "role": "user",
            "content": content,
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
        feedback_id: Optional[str] = None,
    ) -> None:
        """推送流式 chunk 到企微"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            await reply_ctx.ws_client.send_stream_chunk(
                req_id=reply_ctx.req_id,
                stream_id=stream_id,
                content=content,
                finish=finish,
                feedback_id=feedback_id,
            )
        elif reply_ctx.channel == "app" and finish:
            await self._send_app_message(reply_ctx, content)

    async def _reply_text(
        self, reply_ctx: WecomReplyContext, text: str
    ) -> None:
        """发送文本回复（有活跃 stream 时用 stream finish 替换占位内容）"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            if reply_ctx.active_stream_id:
                # 用 stream finish 替换"正在思考..."占位
                await reply_ctx.ws_client.send_stream_chunk(
                    req_id=reply_ctx.req_id,
                    stream_id=reply_ctx.active_stream_id,
                    content=text,
                    finish=True,
                )
                reply_ctx.active_stream_id = None
            else:
                await reply_ctx.ws_client.send_reply(
                    req_id=reply_ctx.req_id,
                    msgtype="text",
                    content={"content": text},
                )
        elif reply_ctx.channel == "app":
            await self._send_app_message(reply_ctx, text)

    async def _reply_credits_insufficient(
        self, reply_ctx: WecomReplyContext,
        needed: int, balance: int, action: str,
    ) -> None:
        """积分不足时回复模板卡片（智能机器人）或文本（自建应用）"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            from services.wecom.card_builder import WecomCardBuilder
            card = WecomCardBuilder.credits_insufficient_card(needed, balance, action)
            await reply_ctx.ws_client.send_template_card(reply_ctx.req_id, card)
        else:
            await self._reply_text(
                reply_ctx,
                f"积分不足，生成{action}需要 {needed} 积分，当前余额 {balance}。",
            )

    async def _send_app_message(
        self, reply_ctx: WecomReplyContext, text: str
    ) -> None:
        """自建应用消息发送：格式适配 + 长消息分割"""
        from services.wecom.app_message_sender import (
            send_text, send_markdown,
        )
        from services.wecom.markdown_adapter import adapt_for_app, split_long_message

        adapted, msgtype = adapt_for_app(text)
        chunks = split_long_message(adapted, max_bytes=2000)

        uid, aid = reply_ctx.wecom_userid, reply_ctx.agent_id
        for i, chunk in enumerate(chunks):
            sent = False
            if msgtype == "markdown":
                sent = await send_markdown(wecom_userid=uid, content=chunk, agent_id=aid)
            if not sent:
                await send_text(wecom_userid=uid, content=chunk, agent_id=aid)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)

    # ── Web 前端通知 ─────────────────────────────────────

    @staticmethod
    async def _notify_web_conversation_updated(
        user_id: str, conversation_id: str,
    ) -> None:
        """通知 Web 前端：企微对话有新消息，刷新对话列表"""
        try:
            await ws_manager.send_to_user(user_id, {
                "type": "conversation_updated",
                "conversation_id": conversation_id,
            })
        except Exception as e:
            logger.warning(
                f"WS notify conversation_updated failed | "
                f"user_id={user_id} | error={e}"
            )

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
