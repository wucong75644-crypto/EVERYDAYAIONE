"""企业微信入站生成前的积分读取能力。"""


class WecomAIMixin:
    """企微入站余额校验（被 WecomMessageService 继承）。"""

    def _get_user_balance(self, user_id: str) -> int:
        """获取用户积分余额"""
        result = self.db.table("users").select("credits").eq(
            "id", user_id,
        ).single().execute()
        return result.data.get("credits", 0) if result.data else 0
