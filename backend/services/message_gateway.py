"""统一消息网关 — 先存后推（Write First, Fan-out Second）

所有消息（不管来源）统一走这个入口：
1. 存入 messages 表（单一事实源）
2. 推 Web（WebSocket 通知前端刷新）
3. 推企微（PushDispatcher → Redis → ws_runner → 企微 WS）
   — 跳过来源渠道，防重复

使用场景：
- 定时任务结果推送（save_system_message）
- 报错/告警通知推送（save_system_message）
- Web 端 AI 回复同步到企微（fanout_to_wecom）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class MessageGateway:
    """统一消息网关"""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def save_system_message(
        self,
        user_id: str,
        org_id: str,
        text: str,
        source: str = "system",
        skip_wecom: bool = False,
        skip_web: bool = False,
    ) -> Optional[str]:
        """存储系统消息（定时任务结果/报错通知）到用户的企微对话，并扇出通知。

        Args:
            user_id: 系统用户 ID
            org_id: 企业 ID
            text: 消息文本
            source: 消息来源标识（system/scheduled_task/error_alert），用于日志
            skip_wecom: 是否跳过企微推送（已由调用方推过时设 True）
            skip_web: 是否跳过 Web 推送

        Returns:
            message_id 或 None（存储失败时）
        """
        if not text:
            return None

        # 1. 查找或创建企微对话
        conversation_id = await self._get_or_create_wecom_conversation(
            user_id, org_id,
        )
        if not conversation_id:
            logger.warning(
                f"MessageGateway: cannot find/create conversation | "
                f"user_id={user_id} | source={source}"
            )
            return None

        # 2. 存 messages 表（显式传 org_id，防止非 OrgScopedDB 场景下 org_id 为 NULL）
        message_id = await self._insert_message(
            conversation_id=conversation_id,
            role="assistant",
            content=[{"type": "text", "text": text}],
            org_id=org_id,
        )
        if not message_id:
            return None

        # 3. 更新对话预览
        await self._update_conversation_preview(conversation_id, text)

        # 4. 扇出：推 Web
        if not skip_web:
            await self._notify_web(user_id, conversation_id, org_id)

        # 5. 扇出：推企微（如果调用方没有已经推过）
        if not skip_wecom:
            await self._push_to_wecom(user_id, org_id, text)

        logger.info(
            f"MessageGateway: saved | source={source} | "
            f"user_id={user_id} | msg_id={message_id} | "
            f"skip_wecom={skip_wecom} | skip_web={skip_web}"
        )
        return message_id

    async def fanout_to_wecom(
        self,
        user_id: str,
        org_id: str,
        text: str,
    ) -> bool:
        """将已存入 DB 的消息推送到企微（Web→企微同步专用）。

        不存消息（调用方已存），只做推送。

        Returns:
            是否推送成功
        """
        return await self._push_to_wecom(user_id, org_id, text)

    # ── 内部方法 ──────────────────────────────────────────────

    async def _get_or_create_wecom_conversation(
        self,
        user_id: str,
        org_id: str,
    ) -> Optional[str]:
        """查找用户最近的企微对话，不存在则创建。"""
        try:
            query = (
                self.db.table("conversations")
                .select("id")
                .eq("user_id", user_id)
                .eq("source", "wecom")
            )
            if org_id:
                query = query.eq("org_id", org_id)
            else:
                query = query.is_("org_id", "null")

            result = (
                query.order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["id"]

            # 创建新企微对话
            from services.conversation_service import ConversationService
            conv_svc = ConversationService(self.db)
            conv = await conv_svc.create_conversation(
                user_id=user_id,
                title="企微对话",
                model_id="auto",
                org_id=org_id,
                source="wecom",
            )
            return conv["id"]
        except Exception as e:
            logger.error(
                f"MessageGateway: conversation get/create failed | "
                f"user_id={user_id} | error={e}"
            )
            return None

    async def _insert_message(
        self,
        conversation_id: str,
        role: str,
        content: List[Dict[str, str]],
        org_id: Optional[str] = None,
    ) -> Optional[str]:
        """插入消息到 messages 表。

        显式传入 org_id 确保多租户隔离（调用方的 db 可能不是 OrgScopedDB）。
        """
        try:
            data: Dict[str, Any] = {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "status": "completed",
            }
            if org_id:
                data["org_id"] = org_id
            result = self.db.table("messages").insert(data).execute()
            if result.data:
                # 更新消息计数
                try:
                    self.db.rpc("increment_message_count", {
                        "conv_id": conversation_id,
                        "p_org_id": getattr(self.db, "org_id", None),
                    }).execute()
                except Exception:
                    pass
                return result.data[0]["id"]
            return None
        except Exception as e:
            logger.error(
                f"MessageGateway: insert message failed | "
                f"conversation_id={conversation_id} | error={e}"
            )
            return None

    async def _update_conversation_preview(
        self, conversation_id: str, text: str,
    ) -> None:
        """更新对话列表预览文本。"""
        try:
            self.db.table("conversations").update({
                "last_message_preview": text[:50],
            }).eq("id", conversation_id).execute()
        except Exception as e:
            logger.warning(
                f"MessageGateway: update preview failed | "
                f"conversation_id={conversation_id} | error={e}"
            )

    @staticmethod
    async def _notify_web(
        user_id: str, conversation_id: str, org_id: Optional[str],
    ) -> None:
        """通知 Web 前端对话有更新。"""
        try:
            from services.websocket_manager import ws_manager
            await ws_manager.send_to_user(user_id, {
                "type": "conversation_updated",
                "conversation_id": conversation_id,
            }, org_id=org_id)
        except Exception as e:
            logger.warning(
                f"MessageGateway: web notify failed | "
                f"user_id={user_id} | error={e}"
            )

    async def _push_to_wecom(
        self,
        user_id: str,
        org_id: str,
        text: str,
    ) -> bool:
        """通过 PushDispatcher 推送到企微。"""
        try:
            # Markdown 清理：企微智能机器人对部分 Markdown 语法支持有限
            from services.wecom.markdown_adapter import clean_for_stream
            text = clean_for_stream(text)

            # 查 user_id 对应的 wecom_userid
            mapping = (
                self.db.table("wecom_user_mappings")
                .select("wecom_userid")
                .eq("user_id", user_id)
                .eq("org_id", org_id)
                .limit(1)
                .execute()
            )
            if not mapping or not mapping.data:
                logger.debug(
                    f"MessageGateway: no wecom mapping | user_id={user_id}"
                )
                return False

            wecom_userid = mapping.data[0]["wecom_userid"]

            from services.scheduler.push_dispatcher import push_dispatcher
            status = await push_dispatcher.dispatch(
                org_id=org_id,
                target={
                    "type": "wecom_user",
                    "wecom_userid": wecom_userid,
                },
                text=text,
                files=[],
            )
            return status == "pushed"
        except Exception as e:
            logger.warning(
                f"MessageGateway: wecom push failed | "
                f"user_id={user_id} | error={e}"
            )
            return False
