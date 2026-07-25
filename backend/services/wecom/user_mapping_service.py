"""
企业微信用户映射服务

将企微 userid 映射到系统 user_id。
首次接收到企微用户消息时自动创建系统账号并建立映射。
"""

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

        安全门面在同一事务校验数据库角色、org/corp、用户映射、企业成员和注册积分，
        并通过 advisory lock + 唯一索引保证并发首次消息只创建一个用户。
        """
        display_name = nickname or f"企微用户_{wecom_userid[:8]}"

        result = self.db.rpc(
            "resolve_wecom_ingress_user",
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
                f"resolve_wecom_ingress_user RPC 失败 | wecom_userid={wecom_userid} | "
                f"result={data}"
            )

        is_new = data.get("is_new", False)
        if is_new:
            logger.info(
                f"Wecom user created (atomic RPC) | wecom_userid={wecom_userid} | "
                f"corp_id={corp_id} | channel={channel} | user_id={user_id}"
            )
        else:
            logger.debug(
                f"Wecom user found (slow path / concurrent loser) | "
                f"wecom_userid={wecom_userid} | user_id={user_id}"
            )

        return user_id

    async def refresh_display_name(
        self,
        *,
        user_id: str,
        wecom_userid: str,
        corp_id: str,
        org_id: str,
        nickname: Optional[str] = None,
    ) -> None:
        """在消息 Actor Scope 绑定后解析并安全更新企微显示名。"""
        display_name = nickname
        if not display_name:
            from services.wecom.wecom_contact_api import fetch_wecom_real_name
            display_name = await fetch_wecom_real_name(
                self.db, org_id, wecom_userid,
            )
        if not display_name:
            return
        try:
            self.db.rpc("update_wecom_ingress_display_name", {
                "p_user_id": user_id,
                "p_wecom_userid": wecom_userid,
                "p_corp_id": corp_id,
                "p_org_id": org_id,
                "p_display_name": display_name,
            }).execute()
        except Exception as error:
            logger.warning(
                "Wecom display name refresh failed | "
                f"user_id={user_id} | error={type(error).__name__}"
            )

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
        chatid: str, chattype: str, org_id: str,
    ) -> None:
        """更新用户最近一次活跃的 chatid（主动推送时用于寻址）"""
        try:
            self.db.rpc("update_wecom_ingress_chat_address", {
                "p_wecom_userid": wecom_userid,
                "p_corp_id": corp_id,
                "p_chatid": chatid,
                "p_chattype": chattype,
                "p_org_id": org_id,
            }).execute()
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
        """通过安全门面登记聊天目标，供定时任务选择推送目标。"""
        try:
            self.db.rpc("upsert_wecom_ingress_chat_target", {
                "p_chatid": chatid,
                "p_chattype": chattype,
                "p_corp_id": corp_id,
                "p_org_id": org_id,
            }).execute()
        except Exception as e:
            logger.warning(
                f"Upsert chat target failed | chatid={chatid} | error={e}"
            )
