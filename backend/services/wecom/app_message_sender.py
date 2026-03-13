"""
企业微信自建应用 — 消息发送 API

通过 access_token 调用企微 API 向用户推送消息（私聊回复）。
API 文档：https://developer.work.weixin.qq.com/document/path/90236

支持消息类型：
- 文本消息（text）
- Markdown 消息（markdown）
"""

from typing import Optional

import httpx
from loguru import logger

from core.config import get_settings
from services.wecom.access_token_manager import get_access_token

SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


async def send_text(
    wecom_userid: str,
    content: str,
    agent_id: Optional[int] = None,
) -> bool:
    """
    发送文本消息给企微用户。

    Args:
        wecom_userid: 企微用户 ID
        content: 文本内容
        agent_id: 应用 AgentID（默认从配置读取）

    Returns:
        发送是否成功
    """
    if agent_id is None:
        agent_id = get_settings().wecom_agent_id

    payload = {
        "touser": wecom_userid,
        "msgtype": "text",
        "agentid": agent_id,
        "text": {"content": content},
    }
    return await _send(payload)


async def send_markdown(
    wecom_userid: str,
    content: str,
    agent_id: Optional[int] = None,
) -> bool:
    """
    发送 Markdown 消息给企微用户。

    Args:
        wecom_userid: 企微用户 ID
        content: Markdown 内容
        agent_id: 应用 AgentID（默认从配置读取）

    Returns:
        发送是否成功
    """
    if agent_id is None:
        agent_id = get_settings().wecom_agent_id

    payload = {
        "touser": wecom_userid,
        "msgtype": "markdown",
        "agentid": agent_id,
        "markdown": {"content": content},
    }
    return await _send(payload)


async def _send(payload: dict) -> bool:
    """发送消息到企微 API"""
    token = await get_access_token()
    if not token:
        logger.error("Wecom send: no access_token available")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                SEND_MSG_URL,
                params={"access_token": token},
                json=payload,
            )
            data = resp.json()

        errcode = data.get("errcode", -1)
        if errcode != 0:
            errmsg = data.get("errmsg", "unknown")
            logger.warning(
                f"Wecom send: API error | errcode={errcode} | "
                f"errmsg={errmsg} | touser={payload.get('touser')}"
            )
            return False

        logger.debug(
            f"Wecom send: OK | touser={payload.get('touser')} | "
            f"msgtype={payload.get('msgtype')}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Wecom send: request failed | error={e} | "
            f"touser={payload.get('touser')}"
        )
        return False
