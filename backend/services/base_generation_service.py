"""
生成服务基类

封装图像/视频生成服务的公共逻辑，包括用户获取、积分扣除等。
"""

from typing import Dict, Any, Optional

from loguru import logger
from supabase import Client

from core.config import Settings, get_settings
from core.exceptions import NotFoundError


class BaseGenerationService:
    """生成服务基类"""

    def __init__(self, db: Client):
        """
        初始化服务

        Args:
            db: Supabase 数据库客户端
        """
        self.db = db
        self.settings: Settings = get_settings()

    async def _get_user(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户信息

        Args:
            user_id: 用户 ID

        Returns:
            用户信息字典

        Raises:
            NotFoundError: 用户不存在
        """
        response = (
            self.db.table("users")
            .select("id, credits")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not response.data:
            raise NotFoundError("用户")

        return response.data

    async def _deduct_credits(
        self,
        user_id: str,
        credits: int,
        description: str,
        change_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        扣除用户积分

        Args:
            user_id: 用户 ID
            credits: 扣除的积分数
            description: 描述
            change_type: 变更类型
            metadata: 额外元数据（未来扩展用）

        Returns:
            扣除后的余额
        """
        # 1. 获取当前积分
        result = (
            self.db.table("users")
            .select("credits")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not result.data:
            raise NotFoundError("用户")

        current_credits = result.data["credits"]
        new_balance = current_credits - credits

        # 2. 更新用户积分
        self.db.table("users").update({
            "credits": new_balance
        }).eq("id", user_id).execute()

        # 3. 记录积分历史
        self.db.table("credits_history").insert({
            "user_id": user_id,
            "change_amount": -credits,
            "balance_after": new_balance,
            "change_type": change_type,
            "description": description,
        }).execute()

        logger.info(
            f"Credits deducted: user_id={user_id}, credits={credits}, "
            f"balance_after={new_balance}, description={description}"
        )

        return new_balance
