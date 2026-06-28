"""
企业微信用户映射服务

将企微 userid 映射到系统 user_id。
首次接收到企微用户消息时自动创建系统账号并建立映射。
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger


from core.config import get_settings


class WecomUserMappingService:
    """企微用户 → 系统用户映射"""

    def __init__(self, db):
        self.db = db
        self.settings = get_settings()

    async def get_or_create_user(
        self,
        wecom_userid: str,
        corp_id: str,
        channel: str = "smart_robot",
        nickname: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> str:
        """
        查找或创建企微用户对应的系统用户。

        并发安全保证（commit 之后）：
        1. 快速路径：先在应用层查 mapping（避免 RPC 开销）
        2. 慢路径：调 RPC wecom_get_or_create_user
           - PG advisory_xact_lock 串行化同 (wecom_userid, corp_id) 的并发请求
           - INSERT user + mapping + credits_history 单事务，任一失败全回滚
           - wecom_mappings_uniq_idx 唯一索引兜底
        3. is_new + org_id：独立加入企业（不在 RPC 内，因为可能失败但不应阻塞登录）
        """
        # 1. 快速路径：直接查现有 mapping
        mapping = await self._find_mapping(wecom_userid, corp_id, org_id=org_id)
        if mapping:
            user_id = mapping["user_id"]
            try:
                self.db.table("users").update({
                    "last_login_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", user_id).execute()
            except Exception as e:
                logger.warning(
                    f"Refresh last_login_at failed | user_id={user_id} | error={e}"
                )
            logger.debug(
                f"Wecom user found (fast path) | wecom_userid={wecom_userid} | "
                f"user_id={user_id}"
            )
            return user_id

        # 2. 慢路径：解析昵称 → 走原子 RPC
        display_name = await self._resolve_display_name(nickname, wecom_userid, org_id)

        result = self.db.rpc(
            "wecom_get_or_create_user",
            {
                "p_wecom_userid": wecom_userid,
                "p_corp_id": corp_id,
                "p_org_id": org_id,
                "p_channel": channel,
                "p_display_name": display_name,
            },
        ).execute()

        data = result.data or {}
        user_id = data.get("user_id")
        if not user_id:
            raise RuntimeError(
                f"wecom_get_or_create_user RPC 失败 | wecom_userid={wecom_userid} | "
                f"result={data}"
            )

        is_new = data.get("is_new", False)
        if is_new:
            logger.info(
                f"Wecom user created (atomic RPC) | wecom_userid={wecom_userid} | "
                f"corp_id={corp_id} | channel={channel} | user_id={user_id}"
            )
            # 加入企业（独立操作：失败告警但不阻塞登录）
            if org_id:
                self._ensure_org_member_safe(user_id, org_id)
        else:
            logger.debug(
                f"Wecom user found (slow path / concurrent loser) | "
                f"wecom_userid={wecom_userid} | user_id={user_id}"
            )

        return user_id

    async def _resolve_display_name(
        self,
        nickname: Optional[str],
        wecom_userid: str,
        org_id: Optional[str],
    ) -> str:
        """解析企微用户的显示昵称（按优先级）"""
        real_name = nickname
        if not real_name and org_id:
            from services.wecom.wecom_contact_api import fetch_wecom_real_name
            real_name = await fetch_wecom_real_name(self.db, org_id, wecom_userid)
        return real_name or f"企微用户_{wecom_userid[:8]}"

    def _ensure_org_member_safe(self, user_id: str, org_id: str) -> None:
        """加入企业成员（失败仅告警，不阻塞登录链路）"""
        try:
            existing = (
                self.db.table("org_members")
                .select("user_id")
                .eq("user_id", user_id)
                .eq("org_id", org_id)
                .maybe_single()
                .execute()
            )
            if existing and existing.data:
                return
            self.db.table("org_members").insert({
                "org_id": org_id,
                "user_id": user_id,
                "role": "member",
                "status": "active",
            }).execute()
        except Exception as e:
            logger.error(
                f"Auto add org member failed | "
                f"org_id={org_id} | user_id={user_id} | error={e}"
            )

    async def _find_mapping(
        self, wecom_userid: str, corp_id: str, org_id: str | None = None,
    ) -> Optional[dict]:
        """查找已有的企微→系统用户映射"""
        query = (
            self.db.table("wecom_user_mappings")
            .select("user_id, wecom_nickname")
            .eq("wecom_userid", wecom_userid)
            .eq("corp_id", corp_id)
        )
        if org_id:
            query = query.eq("org_id", org_id)
        else:
            query = query.is_("org_id", "null")
        result = query.limit(1).execute()
        return result.data[0] if result.data else None

    async def update_nickname(
        self, wecom_userid: str, corp_id: str, nickname: str
    ) -> None:
        """更新企微用户昵称缓存"""
        try:
            self.db.table("wecom_user_mappings").update({
                "wecom_nickname": nickname,
            }).eq("wecom_userid", wecom_userid).eq("corp_id", corp_id).execute()
        except Exception as e:
            logger.warning(
                f"Wecom nickname update failed | wecom_userid={wecom_userid} | "
                f"error={e}"
            )

    async def update_last_chatid(
        self, wecom_userid: str, corp_id: str,
        chatid: str, chattype: str,
    ) -> None:
        """更新用户最近一次活跃的 chatid（主动推送时用于寻址）"""
        try:
            self.db.table("wecom_user_mappings").update({
                "last_chatid": chatid,
                "last_chat_type": chattype,
            }).eq("wecom_userid", wecom_userid).eq("corp_id", corp_id).execute()
        except Exception as e:
            logger.warning(
                f"Wecom chatid update failed | wecom_userid={wecom_userid} | "
                f"error={e}"
            )

    async def get_chatid_by_user_id(self, user_id: str) -> Optional[dict]:
        """通过系统 user_id 查找最近活跃的 chatid

        Returns:
            {"chatid": "...", "chattype": "...", "wecom_userid": "..."} 或 None
        """
        try:
            result = (
                self.db.table("wecom_user_mappings")
                .select("wecom_userid, last_chatid, last_chat_type")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if not result.data:
                return None
            row = result.data[0]
            if not row.get("last_chatid"):
                return None
            return {
                "chatid": row["last_chatid"],
                "chattype": row.get("last_chat_type", "single"),
                "wecom_userid": row["wecom_userid"],
            }
        except Exception as e:
            logger.warning(f"Wecom chatid lookup failed | user_id={user_id} | error={e}")
            return None

    async def upsert_chat_target(
        self, chatid: str, chattype: str, corp_id: str,
        org_id: Optional[str] = None,
    ) -> None:
        """记录聊天目标（群聊/私聊），用于定时任务推送目标选择。

        upsert 逻辑：已存在则更新活跃时间和消息计数，不存在则插入。
        """
        try:
            # 先尝试查询是否已存在
            existing = (
                self.db.table("wecom_chat_targets")
                .select("id, message_count")
                .eq("chatid", chatid)
                .eq("corp_id", corp_id)
                .limit(1)
                .execute()
            )

            if existing.data:
                # 已存在：更新活跃时间和消息计数
                row = existing.data[0]
                self.db.table("wecom_chat_targets").update({
                    "last_active": "now()",
                    "message_count": row["message_count"] + 1,
                    "is_active": True,
                }).eq("id", row["id"]).execute()
            else:
                # 不存在：插入新记录
                insert_data = {
                    "chatid": chatid,
                    "chat_type": chattype,
                    "corp_id": corp_id,
                }
                if org_id:
                    insert_data["org_id"] = org_id
                self.db.table("wecom_chat_targets").insert(insert_data).execute()
                logger.info(
                    f"New chat target discovered | chatid={chatid} | "
                    f"type={chattype} | corp_id={corp_id}"
                )
        except Exception as e:
            logger.warning(
                f"Upsert chat target failed | chatid={chatid} | error={e}"
            )
