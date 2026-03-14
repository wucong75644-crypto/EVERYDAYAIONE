"""
企业微信自建应用 — 消息发送 API

通过 access_token 调用企微 API 向用户推送消息（私聊回复）。
API 文档：https://developer.work.weixin.qq.com/document/path/90236

支持消息类型：
- 文本消息（text）
- Markdown 消息（markdown）
- 图片消息（image）
- 视频消息（video）
- Markdown V2 消息（markdown_v2）
"""

from typing import Optional

import httpx
from loguru import logger

from core.config import get_settings
from services.wecom.access_token_manager import get_access_token

SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
UPLOAD_MEDIA_URL = "https://qyapi.weixin.qq.com/cgi-bin/media/upload"


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


async def send_markdown_v2(
    wecom_userid: str,
    content: str,
    agent_id: Optional[int] = None,
) -> bool:
    """
    发送 Markdown V2 消息给企微用户。

    相比传统 markdown，markdown_v2 支持更多语法：
    代码块、表格、有序/无序列表、分割线等。
    不支持 <@userid> 和 <font color>。

    Args:
        wecom_userid: 企微用户 ID
        content: Markdown V2 内容
        agent_id: 应用 AgentID（默认从配置读取）

    Returns:
        发送是否成功
    """
    if agent_id is None:
        agent_id = get_settings().wecom_agent_id

    payload = {
        "touser": wecom_userid,
        "msgtype": "markdown_v2",
        "agentid": agent_id,
        "markdown_v2": {"content": content},
    }
    return await _send(payload)


async def send_image(
    wecom_userid: str,
    media_id: str,
    agent_id: Optional[int] = None,
) -> bool:
    """
    发送图片消息给企微用户。

    Args:
        wecom_userid: 企微用户 ID
        media_id: 企微临时素材 media_id（通过 upload_temp_media 获取）
        agent_id: 应用 AgentID（默认从配置读取）
    """
    if agent_id is None:
        agent_id = get_settings().wecom_agent_id

    payload = {
        "touser": wecom_userid,
        "msgtype": "image",
        "agentid": agent_id,
        "image": {"media_id": media_id},
    }
    return await _send(payload)


async def send_video(
    wecom_userid: str,
    media_id: str,
    title: str = "",
    description: str = "",
    agent_id: Optional[int] = None,
) -> bool:
    """
    发送视频消息给企微用户。

    Args:
        wecom_userid: 企微用户 ID
        media_id: 企微临时素材 media_id（通过 upload_temp_media 获取）
        title: 视频标题
        description: 视频描述
        agent_id: 应用 AgentID（默认从配置读取）
    """
    if agent_id is None:
        agent_id = get_settings().wecom_agent_id

    payload = {
        "touser": wecom_userid,
        "msgtype": "video",
        "agentid": agent_id,
        "video": {
            "media_id": media_id,
            "title": title,
            "description": description,
        },
    }
    return await _send(payload)


async def upload_temp_media(
    file_url: str,
    media_type: str = "image",
) -> Optional[str]:
    """
    下载文件并上传到企微临时素材库。

    Args:
        file_url: 文件公开 URL（图片或视频）
        media_type: 素材类型，"image" 或 "video"

    Returns:
        media_id（成功）或 None（失败）
    """
    token = await get_access_token()
    if not token:
        logger.error("Wecom upload: no access_token available")
        return None

    timeout = 60.0 if media_type == "video" else 30.0
    ext_map = {"image": "png", "video": "mp4"}
    ext = ext_map.get(media_type, "bin")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 下载文件
            dl_resp = await client.get(file_url)
            dl_resp.raise_for_status()
            file_bytes = dl_resp.content

            content_type = dl_resp.headers.get(
                "content-type", f"{media_type}/{ext}"
            )
            filename = f"media.{ext}"

            # 上传到企微
            upload_resp = await client.post(
                UPLOAD_MEDIA_URL,
                params={"access_token": token, "type": media_type},
                files={"media": (filename, file_bytes, content_type)},
            )
            data = upload_resp.json()

        errcode = data.get("errcode", 0)
        if errcode != 0:
            errmsg = data.get("errmsg", "unknown")
            logger.warning(
                f"Wecom upload: API error | errcode={errcode} | "
                f"errmsg={errmsg} | type={media_type}"
            )
            return None

        media_id = data.get("media_id")
        logger.info(
            f"Wecom upload: OK | type={media_type} | "
            f"media_id={media_id}"
        )
        return media_id

    except Exception as e:
        logger.error(
            f"Wecom upload: failed | type={media_type} | "
            f"url={file_url} | error={e}"
        )
        return None


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
