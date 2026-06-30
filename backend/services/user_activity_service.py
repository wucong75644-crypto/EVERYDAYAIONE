"""用户活跃事件记录。

写入失败只记录 warning，不阻断登录、聊天、上传等主流程。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from loguru import logger


ALLOWED_ACTIVITY_EVENTS = frozenset({
    "login_success",
    "conversation_created",
    "message_sent",
    "task_created",
    "wecom_message_received",
    "file_uploaded",
})

ALLOWED_ACTIVITY_SOURCES = frozenset({"web", "wecom", "system"})


def record_user_activity(
    db: Any,
    *,
    user_id: str,
    event_type: str,
    org_id: str | None = None,
    source: str = "web",
    resource_type: str | None = None,
    resource_id: str | None = None,
    occurred_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """记录用户活跃事件，并更新 users.last_active_at。

    该函数是主流程旁路：任何异常都只记录日志，不向调用方抛出。
    """
    if event_type not in ALLOWED_ACTIVITY_EVENTS:
        logger.warning(f"Skip invalid activity event | event_type={event_type} | user_id={user_id}")
        return
    if source not in ALLOWED_ACTIVITY_SOURCES:
        logger.warning(f"Skip invalid activity source | source={source} | user_id={user_id}")
        return

    event_time = occurred_at or datetime.now(timezone.utc)
    try:
        db.rpc(
            "record_user_activity",
            {
                "p_user_id": user_id,
                "p_event_type": event_type,
                "p_org_id": org_id,
                "p_source": source,
                "p_resource_type": resource_type,
                "p_resource_id": resource_id,
                "p_occurred_at": event_time.isoformat(),
                "p_metadata": dict(metadata or {}),
            },
        ).execute()
    except Exception as e:
        logger.warning(
            f"User activity record failed | user_id={user_id} | "
            f"event_type={event_type} | error={e}"
        )
