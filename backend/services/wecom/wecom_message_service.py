"""
企业微信统一消息处理服务

接收企微消息（来自长连接或回调）→ 映射用户 → 管理对话 →
Agent Loop 路由 → AI 生成 → 流式回复到企微。

两个渠道共用此服务，仅回复方式不同。
"""

import time
from typing import List, Optional

from loguru import logger


from core.config import get_settings
from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.conversation_service import ConversationService
from services.wecom.user_mapping_service import WecomUserMappingService
from services.wecom.wecom_ai_mixin import WecomAIMixin
from services.wecom.wecom_file_mixin import WecomFileMixin
from services.wecom.wecom_ingress_mixin import WecomIngressMixin
from services.wecom.wecom_reply_mixin import WecomReplyMixin


class WecomMessageService(
    WecomIngressMixin, WecomReplyMixin, WecomAIMixin, WecomFileMixin,
):
    """企微消息处理核心：用户映射 → 对话管理 → Agent Loop 路由 → AI 生成 → 回复"""

    def __init__(self, db):
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
            org_id = msg.org_id

            # 用 OrgScopedDB 包装 db（企微回调没有 HTTP 上下文，需手动构造）
            from core.org_scoped_db import OrgScopedDB
            if not isinstance(self.db, OrgScopedDB):
                self.db = OrgScopedDB(self.db, org_id)
                # 同步子服务的 db 引用（不重建实例，避免覆盖外部 mock）
                self._user_svc.db = self.db
                self._conv_svc.db = self.db

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
                corp_id=msg.corp_id,
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
                    chat_type=msg.chattype,
                ):
                    return

            # 3. 多模态资源下载（图片 URL → OSS 永久 URL，需在保存前完成）
            oss_image_urls = await self._download_media(msg, user_id)

            await self._process_incoming_content(
                msg, reply_ctx, user_id, conversation_id, oss_image_urls,
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

    # ── 对话管理 ──────────────────────────────────────────

    async def _get_or_create_conversation(
        self,
        user_id: str,
        chatid: str,
        chattype: str,
        corp_id: str,
        org_id: Optional[str] = None,
    ) -> str:
        """按 provider 会话身份解析稳定的内部 conversation。"""
        try:
            from services.wecom.channel_conversation import (
                resolve_channel_conversation,
            )
            return await resolve_channel_conversation(
                self.db,
                user_id=user_id,
                corp_id=corp_id,
                external_chat_id=chatid,
                chat_type=chattype,
            )

        except Exception as e:
            logger.error(f"Conversation get/create failed | user_id={user_id} | error={e}")
            raise
