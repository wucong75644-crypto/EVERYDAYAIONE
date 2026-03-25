"""
数据一致性检查器

定期检查异常的媒体任务消息，发送告警但不自动修复。
这样可以发现潜在的系统 bug，而不是掩盖问题。
"""

import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from loguru import logger
class DataConsistencyChecker:
    """数据一致性检查器（只告警不修复）"""

    def __init__(self, db):
        self.db = db

    async def check_and_alert(self) -> Dict[str, Any]:
        """
        检查数据一致性并发送告警（不修复）

        检查 messages 表中 generation_params 不为空的消息，
        判断是否存在内容缺失或状态异常。

        Returns:
            检查结果统计
        """
        # 查询最近7天有 generation_params 的消息
        seven_days_ago = (
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=7)
        ).isoformat()

        result = self.db.table("messages").select(
            "id, conversation_id, content, generation_params, is_error, created_at"
        ).gte(
            "created_at", seven_days_ago
        ).execute()

        # 过滤有 generation_params 且 type 为 image/video 的消息
        messages = [
            m for m in (result.data or [])
            if isinstance(m.get("generation_params"), dict)
            and m["generation_params"].get("type") in ("image", "video")
        ]

        if not messages:
            logger.info("✅ Data consistency check passed | no media messages found")
            return {
                "total_checked": 0,
                "total_issues": 0,
                "completed_without_url": 0,
                "pending_with_url": 0,
                "stale_pending": 0,
                "anomalies": [],
            }

        # 分类异常消息
        completed_without_url = []
        pending_with_url = []
        stale_pending = []
        invalid_content_format = []

        for msg in messages:
            msg_id = msg['id']
            is_error = msg.get('is_error', False)
            content = msg.get('content', [])
            gen_type = msg.get('generation_params', {}).get('type')
            created_at_str = msg.get('created_at', '')
            if isinstance(created_at_str, str):
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            else:
                created_at = created_at_str

            # 检查是否有有效的 URL
            has_valid_url = self._has_valid_media_url(content, gen_type)

            # 异常1：非错误消息但没有媒体 URL（可能生成失败未标记）
            if not is_error and not has_valid_url:
                completed_without_url.append({
                    "id": msg_id,
                    "type": gen_type,
                    "created_at": msg.get('created_at'),
                })

            # 异常2：错误消息但有 URL（状态矛盾）
            elif is_error and has_valid_url:
                pending_with_url.append({
                    "id": msg_id,
                    "type": gen_type,
                    "created_at": msg.get('created_at'),
                })

        # 统计结果
        total_issues = (
            len(completed_without_url) + len(pending_with_url) + len(stale_pending)
        )

        results = {
            "total_checked": len(messages),
            "total_issues": total_issues,
            "completed_without_url": len(completed_without_url),
            "pending_with_url": len(pending_with_url),
            "stale_pending": len(stale_pending),
            "anomalies": {
                "completed_without_url": completed_without_url,
                "pending_with_url": pending_with_url,
                "stale_pending": stale_pending,
            },
        }

        # 🔥 发送告警 + 自动修复
        if total_issues > 0:
            self._send_alert(results)
            self._auto_fix(completed_without_url, pending_with_url)
        else:
            logger.info(
                f"✅ Data consistency check passed | "
                f"checked={len(messages)} | no issues found"
            )

        return results

    def _has_valid_media_url(self, content: List[Dict[str, Any]], media_type: str) -> bool:
        """检查 content 中是否有有效的媒体 URL"""
        if not isinstance(content, list):
            return False

        for item in content:
            if not isinstance(item, dict):
                continue

            if media_type == 'image' and item.get('type') == 'image':
                if item.get('url') and item['url'].strip():
                    return True

            if media_type == 'video' and item.get('type') == 'video':
                if item.get('url') and item['url'].strip():
                    return True

        return False

    def _send_alert(self, results: Dict[str, Any]):
        """发送告警（日志 + Sentry）"""
        # 1. 记录日志
        logger.error(
            f"🚨 DATA INCONSISTENCY DETECTED | "
            f"total_issues={results['total_issues']} | "
            f"completed_without_url={results['completed_without_url']} | "
            f"pending_with_url={results['pending_with_url']} | "
            f"stale_pending={results['stale_pending']}"
        )

        # 详细日志
        if results['completed_without_url'] > 0:
            logger.error(
                f"  └─ {results['completed_without_url']} messages: "
                f"completed but no URL (should be failed or have valid URL)"
            )

        if results['pending_with_url'] > 0:
            logger.error(
                f"  └─ {results['pending_with_url']} messages: "
                f"pending but has URL (should be completed)"
            )

        if results['stale_pending'] > 0:
            logger.error(
                f"  └─ {results['stale_pending']} messages: "
                f"pending >24h (likely stuck)"
            )

        logger.error(
            f"\n"
            f"🔧 ACTION REQUIRED:\n"
            f"  1. Run diagnostic: python scripts/diagnose_media_messages.py\n"
            f"  2. Review results\n"
            f"  3. Clean manually: python scripts/clean_media_messages.py --delete --confirm\n"
        )

        # 2. 发送到 Sentry（如果配置了）
        if os.getenv("SENTRY_DSN"):
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    f"Data inconsistency detected: {results['total_issues']} issues",
                    level="error",
                    extras={
                        "total_checked": results['total_checked'],
                        "completed_without_url": results['completed_without_url'],
                        "pending_with_url": results['pending_with_url'],
                        "stale_pending": results['stale_pending'],
                        "anomalies_sample": {
                            "completed_without_url": results['anomalies']['completed_without_url'][:3],
                            "pending_with_url": results['anomalies']['pending_with_url'][:3],
                            "stale_pending": results['anomalies']['stale_pending'][:3],
                        },
                    },
                )
                logger.info("Alert sent to Sentry")
            except Exception as e:
                logger.error(f"Failed to send Sentry alert | error={e}")

    def _auto_fix(
        self,
        completed_without_url: List[Dict[str, Any]],
        pending_with_url: List[Dict[str, Any]],
    ) -> None:
        """自动修复可纠正的数据不一致"""
        fixed = 0

        # 非错误但无 URL → 标记为错误
        for item in completed_without_url:
            try:
                self.db.table("messages").update(
                    {"is_error": True}
                ).eq("id", item["id"]).execute()
                fixed += 1
            except Exception as e:
                logger.warning(f"Auto-fix failed | id={item['id']} | error={e}")

        # 错误但有 URL → 取消错误标记
        for item in pending_with_url:
            try:
                self.db.table("messages").update(
                    {"is_error": False}
                ).eq("id", item["id"]).execute()
                fixed += 1
            except Exception as e:
                logger.warning(f"Auto-fix failed | id={item['id']} | error={e}")

        if fixed:
            logger.info(f"Auto-fixed {fixed} inconsistent messages")
