"""
企业微信自建应用 — 消息发送 API（per-org 版）

通过 access_token 调用企微 API 向用户推送消息（私聊回复）。
每个企业使用自己的 corp_id / agent_id / agent_secret。

API 文档：https://developer.work.weixin.qq.com/document/path/90236
"""

from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from services.wecom.access_token_manager import get_access_token

SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
UPLOAD_MEDIA_URL = "https://qyapi.weixin.qq.com/cgi-bin/media/upload"


@dataclass
class OrgWecomCreds:
    """企业的自建应用凭证（发送消息时必传）"""
    org_id: str
    corp_id: str
    agent_id: int
    agent_secret: str


async def send_text(
    wecom_userid: str,
    content: str,
    creds: OrgWecomCreds,
) -> bool:
    """发送文本消息给企微用户。"""
    payload = {
        "touser": wecom_userid,
        "msgtype": "text",
        "agentid": creds.agent_id,
        "text": {"content": content},
    }
    return await _send(payload, creds)


async def send_markdown(
    wecom_userid: str,
    content: str,
    creds: OrgWecomCreds,
) -> bool:
    """发送 Markdown 消息给企微用户。"""
    payload = {
        "touser": wecom_userid,
        "msgtype": "markdown",
        "agentid": creds.agent_id,
        "markdown": {"content": content},
    }
    return await _send(payload, creds)


async def send_markdown_v2(
    wecom_userid: str,
    content: str,
    creds: OrgWecomCreds,
) -> bool:
    """发送 Markdown V2 消息给企微用户。"""
    payload = {
        "touser": wecom_userid,
        "msgtype": "markdown_v2",
        "agentid": creds.agent_id,
        "markdown_v2": {"content": content},
    }
    return await _send(payload, creds)


async def send_image(
    wecom_userid: str,
    media_id: str,
    creds: OrgWecomCreds,
) -> bool:
    """发送图片消息给企微用户。"""
    payload = {
        "touser": wecom_userid,
        "msgtype": "image",
        "agentid": creds.agent_id,
        "image": {"media_id": media_id},
    }
    return await _send(payload, creds)


async def send_video(
    wecom_userid: str,
    media_id: str,
    creds: OrgWecomCreds,
    title: str = "",
    description: str = "",
) -> bool:
    """发送视频消息给企微用户。"""
    payload = {
        "touser": wecom_userid,
        "msgtype": "video",
        "agentid": creds.agent_id,
        "video": {
            "media_id": media_id,
            "title": title,
            "description": description,
        },
    }
    return await _send(payload, creds)


async def upload_temp_media(
    file_url: str,
    creds: OrgWecomCreds,
    media_type: str = "image",
) -> Optional[str]:
    """下载文件并上传到企微临时素材库。"""
    token = await get_access_token(creds.org_id, creds.corp_id, creds.agent_secret)
    if not token:
        logger.error(f"Wecom upload: no access_token | org_id={creds.org_id}")
        return None

    timeout = 60.0 if media_type == "video" else 30.0
    ext_map = {"image": "png", "video": "mp4"}
    ext = ext_map.get(media_type, "bin")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            dl_resp = await client.get(file_url)
            dl_resp.raise_for_status()
            file_bytes = dl_resp.content

            content_type = dl_resp.headers.get(
                "content-type", f"{media_type}/{ext}"
            )
            filename = f"media.{ext}"

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
                f"Wecom upload: API error | org_id={creds.org_id} | "
                f"errcode={errcode} | errmsg={errmsg} | type={media_type}"
            )
            return None

        media_id = data.get("media_id")
        logger.info(
            f"Wecom upload: OK | org_id={creds.org_id} | type={media_type} | "
            f"media_id={media_id}"
        )
        return media_id

    except Exception as e:
        logger.error(
            f"Wecom upload: failed | org_id={creds.org_id} | type={media_type} | "
            f"url={file_url} | error={e}"
        )
        return None


async def _send(payload: dict, creds: OrgWecomCreds) -> bool:
    """发送消息到企微 API"""
    token = await get_access_token(creds.org_id, creds.corp_id, creds.agent_secret)
    if not token:
        logger.error(f"Wecom send: no access_token | org_id={creds.org_id}")
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
                f"Wecom send: API error | org_id={creds.org_id} | "
                f"errcode={errcode} | errmsg={errmsg} | touser={payload.get('touser')}"
            )
            return False

        logger.debug(
            f"Wecom send: OK | org_id={creds.org_id} | "
            f"touser={payload.get('touser')} | msgtype={payload.get('msgtype')}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Wecom send: request failed | org_id={creds.org_id} | "
            f"error={e} | touser={payload.get('touser')}"
        )
        return False
