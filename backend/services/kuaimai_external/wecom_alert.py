"""
快麦数据变化告警 — 推企微

策略：复用现有 erp_sync_healthcheck._push_to_org_admins 链路
  - 找该 org 的 owner/admin
  - 找他们的 wecom_userid
  - 通过 MessageGateway.save_system_message 推企微

调用方只需提供 org_id + 消息文本。
"""

from __future__ import annotations

from loguru import logger


async def send_alert(org_id: str, markdown_text: str) -> bool:
    """
    给 org 的 owner/admin 发企微告警。

    返回 True/False 仅表示"调用流程完成"，不保证消息真送到（best-effort）。
    内部异常会被吞掉记录日志，不影响主同步流程。

    Args:
        org_id: 企业 ID
        markdown_text: 消息文本（支持企微 markdown 语法）

    Returns:
        False 仅在严重异常（如 DB 完全不可用）时返回；其它都是 True
    """
    try:
        from core.database import get_async_db
        from services.kuaimai.erp_sync_healthcheck import _push_to_org_admins

        async_db = await get_async_db()
        await _push_to_org_admins(async_db, org_id, markdown_text)
        return True
    except Exception as e:
        logger.error(
            f"KuaimaiExternal wecom_alert send failed | "
            f"org={org_id} | error={e}"
        )
        return False
