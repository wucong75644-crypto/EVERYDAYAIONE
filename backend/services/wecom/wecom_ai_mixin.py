"""
企微 AI 能力 Mixin — 记忆注入 + 积分管理 + 媒体发送

提供 WecomMessageService 的 AI 相关能力：
- 记忆预取与注入
- 积分检查与扣除
- 媒体文件发送到企微

AI 生成能力已统一到 ChatHandler.generate_complete()。
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from schemas.message import ContentPart, TextPart
from schemas.wecom import WecomReplyContext


class WecomAIMixin:
    """AI 辅助能力（被 WecomMessageService 继承）"""

    # ── 记忆 ────────────────────────────────────────────

    async def _build_memory_prompt(
        self, user_id: str, query: str, org_id: str | None = None,
    ) -> Optional[str]:
        """构建记忆 system prompt（失败返回 None）"""
        try:
            from services.memory_service import MemoryService
            from services.memory_config import build_memory_system_prompt

            svc = MemoryService(self.db)
            if not await svc.is_memory_enabled(user_id):
                return None

            memories = await svc.get_relevant_memories(user_id, query, org_id=org_id)
            if not memories:
                return None

            return build_memory_system_prompt(memories)
        except Exception as e:
            logger.warning(f"Wecom memory prompt failed | error={e}")
            return None

    # ── 媒体发送 ──────────────────────────────────────────

    async def _send_media_to_wecom(
        self,
        reply_ctx: WecomReplyContext,
        urls: List[str],
        media_type: str,
        message_id: str,
    ) -> None:
        """统一媒体发送（两渠道差异封装）+ 更新 DB 消息"""
        if reply_ctx.channel == "smart_robot" and reply_ctx.ws_client:
            for url in urls:
                if media_type == "image":
                    await reply_ctx.ws_client.send_reply(
                        req_id=reply_ctx.req_id,
                        msgtype="markdown",
                        content={"content": f"![图片]({url})"},
                    )
                else:
                    await reply_ctx.ws_client.send_reply(
                        req_id=reply_ctx.req_id,
                        msgtype="text",
                        content={"content": f"视频已生成：{url}"},
                    )
        elif reply_ctx.channel == "app":
            from services.wecom.app_message_sender import (
                upload_temp_media, send_image, send_video, send_text, OrgWecomCreds,
            )
            creds = OrgWecomCreds(
                org_id=reply_ctx.org_id or "",
                corp_id=reply_ctx.corp_id or "",
                agent_id=reply_ctx.agent_id or 0,
                agent_secret=reply_ctx.agent_secret or "",
            )
            for url in urls:
                media_id = await upload_temp_media(url, creds=creds, media_type=media_type)
                if media_id:
                    if media_type == "image":
                        await send_image(reply_ctx.wecom_userid, media_id, creds=creds)
                    else:
                        await send_video(reply_ctx.wecom_userid, media_id, creds=creds)
                else:
                    label = "图片" if media_type == "image" else "视频"
                    await send_text(reply_ctx.wecom_userid, f"{label}已生成：{url}", creds=creds)

        # 更新 DB 消息（message_id=None 时跳过，由调用方统一保存）
        if message_id:
            content_data = [{"type": media_type, "url": url} for url in urls]
            try:
                self.db.table("messages").update({
                    "content": content_data, "status": "completed",
                }).eq("id", message_id).execute()
            except Exception as e:
                logger.warning(f"Update media message failed | error={e}")

    # ── 积分 ────────────────────────────────────────────

    def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        try:
            result = self.db.table("users").select("credits").eq(
                "id", user_id,
            ).single().execute()
            return result.data.get("credits", 0) if result.data else 0
        except Exception:
            return 0

    def _deduct_credits(
        self, user_id: str, amount: int, reason: str, org_id: str | None = None,
    ) -> None:
        """直接扣除积分（生成成功后调用）"""
        try:
            params = {
                "p_user_id": user_id,
                "p_amount": amount,
                "p_reason": reason,
                "p_change_type": "conversation_cost",
            }
            if org_id:
                params["p_org_id"] = org_id
            self.db.rpc("deduct_credits_atomic", params).execute()
        except Exception as e:
            logger.warning(f"Wecom credit deduction failed | user_id={user_id} | error={e}")
