"""
企微用户重复账号监控

每日通过 Worker 窄能力检查两类异常并告警：
1. 孤儿用户：created_by='wecom' 但 wecom_user_mappings 中无映射
2. 重复身份：同 (wecom_userid, corp_id, org_id) 出现多次

触发条件任一 > 0 时写 logger.error，由 error_alert_sink 自动消费上报 Sentry。
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

from loguru import logger

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)


class WecomDuplicateMonitor:
    """企微重复账号巡检（只告警不修复）"""

    def __init__(self, db):
        self.db = ScopedDatabaseClient(
            db,
            DatabaseScope(
                actor_user_id=None,
                org_id=None,
                access_kind=DatabaseAccessKind.WORKER,
                request_id="wecom-identity-health",
            ),
        )

    async def check_and_alert(self) -> dict[str, Any]:
        """
        执行检查并按需告警。

        Returns:
            { orphan_users: int, duplicate_groups: int, duplicate_samples: [...] }
        """
        payload = await self._load_snapshot()
        orphan_count = payload["orphan_users"]
        duplicate_count = payload["duplicate_groups"]

        if orphan_count == 0 and duplicate_count == 0:
            logger.debug(
                "✅ Wecom dup monitor passed | orphans=0 | duplicate_groups=0"
            )
            return {
                "orphan_users": 0,
                "duplicate_groups": 0,
                "duplicate_samples": [],
            }

        # 异常 → 写 error 触发 Sentry
        if orphan_count > 0:
            logger.error(
                f"🚨 Wecom orphan users detected | count={orphan_count} | "
                f"meaning=created_by='wecom' but no entry in wecom_user_mappings | "
                f"action=check commit cd12ed7 RPC fix is still active"
            )

        if duplicate_count > 0:
            logger.error(
                f"🚨 Wecom duplicate identities detected | "
                f"groups={duplicate_count} | "
                f"action=run scripts/merge_wecom_duplicate_users.py"
            )

        return {
            "orphan_users": orphan_count,
            "duplicate_groups": duplicate_count,
            "duplicate_samples": [],
        }

    async def _load_snapshot(self) -> dict[str, int]:
        """读取数据库内完成统计的全局 Worker 快照。"""
        response = await asyncio.to_thread(
            lambda: self.db.rpc(
                "worker_wecom_identity_health_snapshot",
                {},
            ).execute(),
        )
        if inspect.isawaitable(response):
            response = await response
        payload = response.data if response is not None else None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("orphan_users"), int)
            or not isinstance(payload.get("duplicate_groups"), int)
            or payload["orphan_users"] < 0
            or payload["duplicate_groups"] < 0
        ):
            raise RuntimeError("WECOM_IDENTITY_HEALTH_SNAPSHOT_INVALID")
        return payload
