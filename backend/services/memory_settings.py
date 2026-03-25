"""记忆设置服务 — 用户记忆开关和保留天数管理"""

from typing import Any, Dict

from loguru import logger


from core.config import settings
from core.exceptions import AppException
from services.memory_config import _get_mem0


class MemorySettingsService:
    """用户记忆设置 CRUD"""

    def __init__(self, db):
        self.db = db

    async def get_settings(self, user_id: str) -> Dict[str, Any]:
        """获取用户记忆设置，不存在时自动创建默认记录"""
        try:
            result = (
                self.db.table("user_memory_settings")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            if result.data:
                row = result.data[0]
                return {
                    "memory_enabled": row["memory_enabled"],
                    "retention_days": row["retention_days"],
                    "updated_at": row.get("updated_at"),
                }
            return await self._create_default_settings(user_id)
        except Exception as e:
            logger.error(
                f"Error getting memory settings | user_id={user_id} | error={e}"
            )
            return {
                "memory_enabled": settings.memory_enabled_default,
                "retention_days": 7,
            }

    async def update_settings(
        self, user_id: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """更新用户记忆设置"""
        try:
            await self.get_settings(user_id)

            update_data = {}
            if "memory_enabled" in kwargs and kwargs["memory_enabled"] is not None:
                update_data["memory_enabled"] = kwargs["memory_enabled"]
            if "retention_days" in kwargs and kwargs["retention_days"] is not None:
                update_data["retention_days"] = kwargs["retention_days"]

            if not update_data:
                return await self.get_settings(user_id)

            result = (
                self.db.table("user_memory_settings")
                .update(update_data)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise AppException(
                    code="MEMORY_SETTINGS_UPDATE_ERROR",
                    message="更新记忆设置失败",
                    status_code=500,
                )

            row = result.data[0]
            logger.info(
                f"Memory settings updated | user_id={user_id} | updates={update_data}"
            )
            return {
                "memory_enabled": row["memory_enabled"],
                "retention_days": row["retention_days"],
                "updated_at": row.get("updated_at"),
            }
        except AppException:
            raise
        except Exception as e:
            logger.error(
                f"Error updating memory settings | user_id={user_id} | error={e}"
            )
            raise AppException(
                code="MEMORY_SETTINGS_UPDATE_ERROR",
                message="更新记忆设置失败",
                status_code=500,
            )

    async def is_memory_enabled(self, user_id: str) -> bool:
        """检查用户是否开启了记忆功能"""
        mem0 = await _get_mem0()
        if mem0 is None:
            return False
        s = await self.get_settings(user_id)
        return s.get("memory_enabled", False)

    async def _create_default_settings(self, user_id: str) -> Dict[str, Any]:
        """创建默认记忆设置"""
        try:
            result = (
                self.db.table("user_memory_settings")
                .insert({
                    "user_id": user_id,
                    "memory_enabled": settings.memory_enabled_default,
                    "retention_days": 7,
                })
                .execute()
            )
            if result.data:
                row = result.data[0]
                return {
                    "memory_enabled": row["memory_enabled"],
                    "retention_days": row["retention_days"],
                    "updated_at": row.get("updated_at"),
                }
        except Exception as e:
            logger.warning(
                f"Failed to create default settings | user_id={user_id} | error={e}"
            )
        return {
            "memory_enabled": settings.memory_enabled_default,
            "retention_days": 7,
        }
