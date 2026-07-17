"""企业微信智能机器人回调的统一规范化边界。"""

from __future__ import annotations

from typing import Any

from schemas.wecom import WecomIncomingMessage, WecomMsgType


class WecomMessageNormalizationError(ValueError):
    """回调缺少构造稳定会话身份或消息身份所需的字段。"""


def normalize_wecom_message(
    body: dict[str, Any],
    *,
    org_id: str,
    corp_id: str,
) -> WecomIncomingMessage:
    """把 provider 回调转换为业务层唯一接受的消息结构。"""
    sender = str(body.get("from", {}).get("userid") or "").strip()
    msgid = str(body.get("msgid") or "").strip()
    chattype = str(body.get("chattype") or "single").strip().lower()
    chatid = str(body.get("chatid") or "").strip()
    if not msgid:
        raise WecomMessageNormalizationError("WECOM_MSGID_MISSING")
    if not sender:
        raise WecomMessageNormalizationError("WECOM_SENDER_MISSING")
    if chattype not in ("single", "group"):
        raise WecomMessageNormalizationError("WECOM_CHAT_TYPE_INVALID")
    if not chatid and chattype == "single":
        chatid = sender
    if not chatid:
        raise WecomMessageNormalizationError("WECOM_GROUP_CHATID_MISSING")

    parsed = parse_message_content(body)
    return WecomIncomingMessage(
        msgid=msgid,
        wecom_userid=sender,
        corp_id=corp_id,
        chatid=chatid,
        chattype=chattype,
        msgtype=parsed["msgtype"],
        channel="smart_robot",
        org_id=org_id,
        text_content=parsed["text_content"],
        image_urls=parsed["image_urls"],
        file_url=parsed["file_url"],
        file_name=parsed["file_name"],
        aeskeys=parsed["aeskeys"],
        raw_data=body,
    )


def parse_message_content(body: dict[str, Any]) -> dict[str, Any]:
    """解析各消息类型的内容字段；身份字段由 normalize_wecom_message 处理。"""
    msgtype = str(body.get("msgtype") or "")
    result: dict[str, Any] = {
        "msgtype": msgtype,
        "text_content": None,
        "image_urls": [],
        "file_url": None,
        "file_name": None,
        "aeskeys": {},
    }
    if msgtype in (WecomMsgType.TEXT, WecomMsgType.VOICE):
        result["text_content"] = body.get(msgtype, {}).get("content", "")
    elif msgtype == WecomMsgType.IMAGE:
        _parse_media(body.get("image", {}), result, image=True)
    elif msgtype in (WecomMsgType.FILE, WecomMsgType.VIDEO):
        _parse_media(body.get(msgtype, {}), result)
    elif msgtype == WecomMsgType.MIXED:
        texts: list[str] = []
        for item in body.get("mixed", {}).get("msg_item", []):
            if item.get("type") == "text":
                texts.append(item.get("text", {}).get("content", ""))
            elif item.get("type") == "image":
                _parse_media(item.get("image", {}), result, image=True)
        result["text_content"] = "".join(texts) or None
    return result


def _parse_media(
    media: dict[str, Any],
    result: dict[str, Any],
    *,
    image: bool = False,
) -> None:
    url = str(media.get("url") or "")
    if image:
        if url:
            result["image_urls"].append(url)
    else:
        result["file_url"] = url
        result["file_name"] = str(
            media.get("filename") or media.get("name") or ""
        )
    aeskey = media.get("aeskey")
    if url and aeskey:
        result["aeskeys"][url] = aeskey
