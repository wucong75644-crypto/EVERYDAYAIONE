"""
订阅管理服务

处理用户模型订阅的 CRUD 操作。
不依赖 models 表，模型元数据由前端 ALL_MODELS 维护。
"""

from loguru import logger
from supabase import Client

from core.exceptions import NotFoundError, ValidationError


# 前端 ALL_MODELS 中所有合法的模型 ID（排除 auto）
KNOWN_MODEL_IDS = frozenset([
    "gemini-3-flash",
    "gemini-3-pro",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "deepseek-v3.2",
    "deepseek-r1",
    "qwen3.5-plus",
    "kimi-k2.5",
    "glm-5",
    "openai/gpt-4.1",
    "openai/gpt-4.1-mini",
    "openai/o4-mini",
    "openai/gpt-5.4",
    "openai/gpt-5.4-pro",
    "openai/gpt-5.3-codex",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "x-ai/grok-4.1-fast",
    "google/gemini-3.1-pro-preview",
    "google/nano-banana",
    "google/nano-banana-edit",
    "nano-banana-pro",
    "sora-2-text-to-video",
    "sora-2-image-to-video",
    "sora-2-pro-storyboard",
])



class SubscriptionService:
    """订阅管理服务"""

    def __init__(self, db: Client):
        self.db = db

    def get_all_models(self) -> list[dict]:
        """获取所有模型的状态信息（从代码常量生成，不查 models 表）"""
        return [
            {"id": mid, "status": "active"}
            for mid in KNOWN_MODEL_IDS
        ]

    def get_user_subscriptions(self, user_id: str) -> list[dict]:
        """获取用户已订阅的模型列表"""
        try:
            result = self.db.table("user_subscriptions").select(
                "model_id, subscribed_at"
            ).eq("user_id", user_id).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"获取订阅列表失败 | user_id={user_id} | error={e}")
            return []

    def subscribe(self, user_id: str, model_id: str) -> dict:
        """
        订阅模型（幂等：已订阅则直接返回成功）

        Raises:
            ValidationError: model_id 不在已知模型列表中
        """
        if model_id not in KNOWN_MODEL_IDS:
            raise ValidationError(f"未知的模型: {model_id}")

        # 检查是否已订阅
        existing = self.db.table("user_subscriptions").select("model_id").eq(
            "user_id", user_id
        ).eq("model_id", model_id).execute()

        if existing.data:
            logger.info(f"模型已订阅（幂等） | user_id={user_id} | model_id={model_id}")
            return {"message": "订阅成功", "model_id": model_id}

        # 插入订阅记录
        try:
            self.db.table("user_subscriptions").insert({
                "user_id": user_id,
                "model_id": model_id,
            }).execute()
            logger.info(f"订阅成功 | user_id={user_id} | model_id={model_id}")
            return {"message": "订阅成功", "model_id": model_id}
        except Exception as e:
            logger.error(
                f"订阅失败 | user_id={user_id} | model_id={model_id} | error={e}"
            )
            raise ValidationError("订阅失败，请稍后重试")

    def unsubscribe(self, user_id: str, model_id: str) -> dict:
        """
        取消订阅模型

        Raises:
            NotFoundError: 未订阅该模型
        """
        # 检查是否已订阅
        existing = self.db.table("user_subscriptions").select("model_id").eq(
            "user_id", user_id
        ).eq("model_id", model_id).execute()

        if not existing.data:
            raise NotFoundError("订阅记录", model_id)

        # 删除订阅
        try:
            self.db.table("user_subscriptions").delete().eq(
                "user_id", user_id
            ).eq("model_id", model_id).execute()
            logger.info(f"取消订阅成功 | user_id={user_id} | model_id={model_id}")
            return {"message": "已取消订阅", "model_id": model_id}
        except Exception as e:
            logger.error(
                f"取消订阅失败 | user_id={user_id} | model_id={model_id} | error={e}"
            )
            raise ValidationError("取消订阅失败，请稍后重试")
