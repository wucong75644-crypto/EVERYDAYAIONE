"""
数据一致性检查器

定期检查异常的媒体任务消息，发送告警但不自动修复。
这样可以发现潜在的系统 bug，而不是掩盖问题。
"""

import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from loguru import logger
from supabase import Client


class DataConsistencyChecker:
    """数据一致性检查器（只告警不修复）"""

    def __init__(self, db: Client):
        self.db = db

    async def check_and_alert(self) -> Dict[str, Any]:
        """
        检查数据一致性并发送告警（不修复）

        异常情况：
        1. completed 状态但 content 中没有图片/视频 URL
        2. pending 状态但 content 中有 URL（应该已完成）
        3. 超过24小时的 pending 消息

        Returns:
            检查结果统计
        """
        # 查询所有媒体任务消息（最近7天）
        seven_days_ago = (
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=7)
        ).isoformat()

        result = self.db.table("messages").select(
            "id, conversation_id, status, content, generation_params, task_id, created_at"
        ).in_(
            "generation_params->>type", ["image", "video"]
        ).gte(
            "created_at", seven_days_ago
        ).execute()

        messages = result.data

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
            status = msg['status']
            content = msg.get('content', [])
            gen_type = msg.get('generation_params', {}).get('type')
            task_id = msg.get('task_id')
            created_at = datetime.fromisoformat(msg['created_at'].replace("Z", "+00:00"))

            # 检查是否有有效的 URL
            has_valid_url = self._has_valid_media_url(content, gen_type)

            # 异常1：completed 但没有 URL
            if status == 'completed' and not has_valid_url:
                completed_without_url.append({
                    "id": msg_id,
                    "type": gen_type,
                    "task_id": task_id,
                    "created_at": msg['created_at'],
                })

            # 异常2：pending 但有 URL
            elif status == 'pending' and has_valid_url:
                pending_with_url.append({
                    "id": msg_id,
                    "type": gen_type,
                    "task_id": task_id,
                    "created_at": msg['created_at'],
                })

            # 异常3：超过24小时的 pending
            elif status == 'pending':
                age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
                if age_hours > 24:
                    stale_pending.append({
                        "id": msg_id,
                        "type": gen_type,
                        "task_id": task_id,
                        "age_hours": round(age_hours, 1),
                        "created_at": msg['created_at'],
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

        # 🔥 发送告警（只告警，不修复）
        if total_issues > 0:
            self._send_alert(results)
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
