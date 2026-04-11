"""
企微通讯录单查 API（user/get）

为什么不做全量同步：
- 全量同步 (department/list + user/simplelist) 需要"通讯录管理"权限,
  普通自建应用即使勾了"通讯录读取"也不一定够，企微 2022 后收紧了。
- 项目实际只需要"和机器人交互过的员工"的真名，按需单查 user/get 即可。
- user/get 接口在自建应用 token + 自己可见范围内可调，无需额外权限。

调用入口：
    name = await fetch_wecom_real_name(db, org_id, wecom_userid)

返回 None 表示拿不到（缺凭证 / 网络失败 / 该 userid 不在应用可见范围）。
调用方应该自行兜底。
"""
from __future__ import annotations
from typing import Optional

import httpx
from loguru import logger

from services.org.config_resolver import OrgConfigResolver
from services.wecom.access_token_manager import get_access_token

USER_GET_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/get"


async def fetch_wecom_real_name(
    db,
    org_id: str,
    wecom_userid: str,
    timeout: float = 3.0,
) -> Optional[str]:
    """
    通过企微 user/get 拿单个员工的真实姓名。

    Args:
        db: 裸 db（用于查 organizations 和 org_configs）
        org_id: 企业 ID
        wecom_userid: 企微 userid
        timeout: HTTP 超时（默认 3s）。
                 调用方在消息热路径同步 await，3s 是体验/可靠性的折中:
                 - 太短（<1s）→ 网络抖动时频繁失败 → 走兜底名
                 - 太长（>3s）→ 用户首次发消息体验差
                 与项目其他企微 API 调用对齐（oauth_service / message_sender 都用 3s）

    Returns:
        员工姓名 / None（任何失败都返回 None，由调用方决定兜底）
    """
    if not org_id or not wecom_userid:
        return None

    # 1. 解析凭证（corp_id 来自 organizations，agent_secret 来自 org_configs）
    try:
        org_resp = (
            db.table("organizations")
            .select("wecom_corp_id")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        corp_id = ((org_resp.data or {}).get("wecom_corp_id") or "").strip()
    except Exception as e:
        logger.warning(f"fetch_wecom_real_name: read corp_id failed | org_id={org_id} | error={e}")
        return None

    if not corp_id:
        return None

    try:
        agent_secret = OrgConfigResolver(db).get(org_id, "wecom_agent_secret")
    except Exception as e:
        logger.warning(f"fetch_wecom_real_name: read agent_secret failed | org_id={org_id} | error={e}")
        return None

    if not agent_secret:
        return None

    # 2. 拿 access_token
    token = await get_access_token(org_id, corp_id, agent_secret)
    if not token:
        return None

    # 3. 调 user/get
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                USER_GET_URL,
                params={"access_token": token, "userid": wecom_userid},
            )
            data = resp.json()
    except Exception as e:
        logger.warning(
            f"fetch_wecom_real_name: API call failed | org_id={org_id} | "
            f"wecom_userid={wecom_userid} | error={e}"
        )
        return None

    errcode = data.get("errcode", 0)
    if errcode != 0:
        # 常见 errcode:
        #   60011: 应用不可见，userid 不在应用范围内
        #   40003: invalid userid
        logger.warning(
            f"fetch_wecom_real_name: API errcode | org_id={org_id} | "
            f"wecom_userid={wecom_userid} | errcode={errcode} | errmsg={data.get('errmsg')}"
        )
        return None

    name = (data.get("name") or "").strip()
    return name or None
