"""
企业微信用户映射服务

将企微 userid 映射到系统 user_id。
首次接收到企微用户消息时自动创建系统账号并建立映射。
"""

from typing import Optional

from loguru import logger
from supabase import Client

from core.config import get_settings


class WecomUserMappingService:
    """企微用户 → 系统用户映射"""

    def __init__(self, db: Client):
        self.db = db
        self.settings = get_settings()

    async def get_or_create_user(
        self,
        wecom_userid: str,
        corp_id: str,
        channel: str = "smart_robot",
        nickname: Optional[str] = None,
    ) -> str:
        """
        查找或创建企微用户对应的系统用户。

        Args:
            wecom_userid: 企微用户 ID
            corp_id: 企业 ID
            channel: 渠道来源（smart_robot / app）
            nickname: 企微昵称（可选）

        Returns:
            系统 user_id（UUID 字符串）
        """
        # 1. 查找已有映射
        mapping = await self._find_mapping(wecom_userid, corp_id)
        if mapping:
            logger.debug(
                f"Wecom user found | wecom_userid={wecom_userid} | "
                f"user_id={mapping['user_id']}"
            )
            return mapping["user_id"]

        # 2. 创建新系统用户 + 映射
        user_id = await self._create_wecom_user(
            wecom_userid, corp_id, channel, nickname
        )
        logger.info(
            f"Wecom user created | wecom_userid={wecom_userid} | "
            f"corp_id={corp_id} | channel={channel} | user_id={user_id}"
        )
        return user_id

    async def _find_mapping(
        self, wecom_userid: str, corp_id: str
    ) -> Optional[dict]:
        """查找已有的企微→系统用户映射"""
        try:
            result = (
                self.db.table("wecom_user_mappings")
                .select("user_id, wecom_nickname")
                .eq("wecom_userid", wecom_userid)
                .eq("corp_id", corp_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Wecom mapping query failed | wecom_userid={wecom_userid} | "
                f"error={e}"
            )
            return None

    async def _create_wecom_user(
        self,
        wecom_userid: str,
        corp_id: str,
        channel: str,
        nickname: Optional[str],
    ) -> str:
        """
        创建系统用户 + 企微映射记录。

        用户属性：
        - phone: 空（企微用户无手机号）
        - nickname: 企微昵称或默认名
        - created_by: "wecom"（标识来源）
        - credits: 100（新用户赠送）
        """
        display_name = nickname or f"企微用户_{wecom_userid[:8]}"

        # 创建系统用户
        user_result = (
            self.db.table("users")
            .insert({
                "nickname": display_name,
                "login_methods": ["wecom"],
                "created_by": "wecom",
                "role": "user",
                "credits": 100,
                "status": "active",
            })
            .execute()
        )

        if not user_result.data:
            raise RuntimeError(
                f"Failed to create system user for wecom_userid={wecom_userid}"
            )

        user_id = user_result.data[0]["id"]

        # 记录注册积分
        self.db.table("credits_history").insert({
            "user_id": user_id,
            "change_amount": 100,
            "balance_after": 100,
            "change_type": "register_gift",
            "description": "企业微信用户注册赠送积分",
        }).execute()

        # 创建映射
        self.db.table("wecom_user_mappings").insert({
            "wecom_userid": wecom_userid,
            "corp_id": corp_id,
            "user_id": user_id,
            "channel": channel,
            "wecom_nickname": display_name,
        }).execute()

        return user_id

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
                self.db.table("wecom_chat_targets").insert({
                    "chatid": chatid,
                    "chat_type": chattype,
                    "corp_id": corp_id,
                }).execute()
                logger.info(
                    f"New chat target discovered | chatid={chatid} | "
                    f"type={chattype} | corp_id={corp_id}"
                )
        except Exception as e:
            logger.warning(
                f"Upsert chat target failed | chatid={chatid} | error={e}"
            )
