"""
企微用户重复账号监控

每日检查两类异常并告警：
1. 孤儿用户：created_by='wecom' 但 wecom_user_mappings 中无映射
   → 说明 _create_wecom_user 流程漏了写 mapping 步骤（commit cd12ed7 之前的根因）
2. 重复账号：同 (nickname, created_by='wecom') 出现多次
   → 说明并发竞态又出现了

触发条件任一 > 0 时写 logger.error，由 error_alert_sink 自动消费上报 Sentry。
"""
from __future__ import annotations

from typing import Any

from loguru import logger


class WecomDuplicateMonitor:
    """企微重复账号巡检（只告警不修复）"""

    def __init__(self, db):
        self.db = db

    async def check_and_alert(self) -> dict[str, Any]:
        """
        执行检查并按需告警。

        Returns:
            { orphan_users: int, duplicate_groups: int, duplicate_samples: [...] }
        """
        orphan_count = self._count_orphan_wecom_users()
        dup_groups = self._find_duplicate_groups()

        if orphan_count == 0 and not dup_groups:
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

        if dup_groups:
            samples = ", ".join(
                f"{g['nickname']}×{g['count']}" for g in dup_groups[:5]
            )
            logger.error(
                f"🚨 Wecom duplicate users detected | groups={len(dup_groups)} | "
                f"samples=[{samples}] | "
                f"action=run scripts/merge_wecom_duplicate_users.py"
            )

        return {
            "orphan_users": orphan_count,
            "duplicate_groups": len(dup_groups),
            "duplicate_samples": dup_groups[:10],
        }

    def _count_orphan_wecom_users(self) -> int:
        """统计 created_by='wecom' 但无 mapping 的孤儿用户"""
        try:
            wecom_users = (
                self.db.table("users")
                .select("id")
                .eq("created_by", "wecom")
                .execute()
            )
            if not wecom_users.data:
                return 0

            user_ids = [u["id"] for u in wecom_users.data]
            mapped = (
                self.db.table("wecom_user_mappings")
                .select("user_id")
                .in_("user_id", user_ids)
                .execute()
            )
            mapped_ids = {m["user_id"] for m in (mapped.data or [])}
            return sum(1 for uid in user_ids if uid not in mapped_ids)
        except Exception as e:
            logger.warning(f"Wecom dup monitor: orphan check failed | {e}")
            return 0

    def _find_duplicate_groups(self) -> list[dict]:
        """找同 (nickname, created_by='wecom') 重复组"""
        try:
            result = (
                self.db.table("users")
                .select("nickname")
                .eq("created_by", "wecom")
                .execute()
            )
            counts: dict[str, int] = {}
            for u in (result.data or []):
                nick = u.get("nickname")
                if nick:
                    counts[nick] = counts.get(nick, 0) + 1
            return [
                {"nickname": nick, "count": cnt}
                for nick, cnt in counts.items() if cnt > 1
            ]
        except Exception as e:
            logger.warning(f"Wecom dup monitor: duplicate check failed | {e}")
            return []
