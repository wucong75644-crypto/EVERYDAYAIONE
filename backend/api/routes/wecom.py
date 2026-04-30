"""
企业微信自建应用回调路由

- GET  /api/wecom/callback — URL 验证（企微配置回调 URL 时的验证请求）
- POST /api/wecom/callback — 接收加密消息（立即返回，异步处理）
"""

import asyncio
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

from api.deps import Database, ScopedDB
from core.config import get_settings
from schemas.wecom import (
    WecomChatType,
    WecomIncomingMessage,
    WecomMsgType,
    WecomReplyContext,
)
from services.wecom.crypto import WXBizMsgCrypt
from services.wecom.wecom_message_service import WecomMessageService

router = APIRouter(prefix="/wecom", tags=["企业微信回调"])


def _get_crypt() -> WXBizMsgCrypt:
    """获取加解密器实例"""
    s = get_settings()
    return WXBizMsgCrypt(
        token=s.wecom_token,
        encoding_aes_key=s.wecom_encoding_aes_key,
        corp_id=s.wecom_corp_id,
    )


@router.get("/callback", summary="URL 验证")
async def verify_url(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> PlainTextResponse:
    """
    企微配置回调 URL 时发送的验证请求。

    流程：验签 → 解密 echostr → 返回明文。
    """
    crypt = _get_crypt()
    ret, decrypted = crypt.verify_url(msg_signature, timestamp, nonce, echostr)

    if ret != 0:
        logger.warning(f"Wecom callback: URL verify failed | ret={ret}")
        return PlainTextResponse("verify failed", status_code=403)

    logger.info("Wecom callback: URL verified OK")
    return PlainTextResponse(decrypted)


# TODO(time-context PR3): receive_message + _process_callback_xml 注入 RequestContext
# 目前 ERPAgent 内部用 RequestContext.build() fallback，时区正确但失去"请求级 SSOT"。
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B13/B14)
@router.post("/callback", summary="接收消息")
async def receive_message(
    request: Request,
    db: Database,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> PlainTextResponse:
    """
    接收企微推送的加密消息。

    流程：验签+解密 → 立即返回 "success" → 异步处理消息。
    企微要求 5 秒内响应，否则会重试推送。
    """
    body = await request.body()
    post_data = body.decode("utf-8")

    crypt = _get_crypt()
    ret, xml_content = crypt.decrypt_msg(
        post_data, msg_signature, timestamp, nonce,
    )

    if ret != 0:
        logger.warning(f"Wecom callback: decrypt failed | ret={ret}")
        return PlainTextResponse("decrypt failed", status_code=403)

    # 解析明文 XML → 异步处理
    asyncio.create_task(
        _process_callback_xml(xml_content, db)
    )

    # 立即返回（5 秒限制）
    return PlainTextResponse("success")


async def _process_callback_xml(xml_content: str, db) -> None:
    """解析回调 XML 并异步处理消息"""
    try:
        root = ET.fromstring(xml_content)
        msg_type = _xml_text(root, "MsgType")

        # 事件消息（如关注/进入聊天等），暂不处理
        if msg_type == "event":
            event_type = _xml_text(root, "Event")
            logger.info(f"Wecom callback: event={event_type}, skipped")
            return

        settings = get_settings()
        msgid = _xml_text(root, "MsgId") or _xml_text(root, "NewMsgId") or ""
        from_user = _xml_text(root, "FromUserName") or ""

        # 提取文本内容
        text_content = _xml_text(root, "Content")

        # 从 corp_id 查 org_id（自建应用 corp_id 在 .env 配置，org_id 在 DB 中）
        corp_id = settings.wecom_corp_id or ""
        org_id = None
        if corp_id:
            try:
                result = db.table("organizations").select("id").eq(
                    "wecom_corp_id", corp_id,
                ).limit(1).execute()
                if result.data:
                    org_id = result.data[0]["id"]
            except Exception as e:
                logger.warning(f"Wecom callback: org_id lookup failed | corp_id={corp_id} | error={e}")

        # 构建统一消息格式
        msg = WecomIncomingMessage(
            msgid=msgid,
            wecom_userid=from_user,
            corp_id=corp_id,
            chatid=from_user,  # 私聊场景 chatid=userid
            chattype=WecomChatType.SINGLE,
            msgtype=msg_type or WecomMsgType.TEXT,
            channel="app",
            org_id=org_id,
            text_content=text_content,
        )

        reply_ctx = WecomReplyContext(
            channel="app",
            wecom_userid=from_user,
            org_id=org_id,
            agent_id=settings.wecom_agent_id,
            corp_id=corp_id,
            agent_secret=settings.wecom_agent_secret,
        )

        svc = WecomMessageService(db)
        await svc.handle_message(msg, reply_ctx)

    except Exception as e:
        logger.error(f"Wecom callback: process failed | error={e}")


def _xml_text(root: ET.Element, tag: str) -> str | None:
    """安全提取 XML 子节点文本"""
    node = root.find(tag)
    return node.text if node is not None else None


# ── 主动推送 API ──────────────────────────────────────────

from pydantic import BaseModel, Field


class WecomPushRequest(BaseModel):
    """主动推送消息请求体"""
    user_id: str = Field(description="系统用户 ID")
    org_id: str = Field(description="企业 ID（用于找到对应的 WS 客户端）")
    message: str = Field(description="消息内容（Markdown 格式）")
    chatid: str | None = Field(default=None, description="指定 chatid（不填则自动查找）")
    msgtype: str = Field(default="markdown", description="消息类型")


@router.post("/push", summary="主动推送消息")
async def push_message(req: WecomPushRequest, db: Database) -> dict:
    """主动推送消息到企微用户（内部调用）

    通过 WS 长连接的 aibot_send_msg 向指定用户发送消息。
    """
    from services.wecom.user_mapping_service import WecomUserMappingService

    # 1. 获取该企业的 ws_client 实例
    from wecom_ws_runner import get_ws_client
    ws_client = get_ws_client(req.org_id)
    if not ws_client or not ws_client.is_connected:
        return {"success": False, "error": "该企业的 WS 长连接未就绪"}

    # 2. 确定 chatid
    from core.org_scoped_db import OrgScopedDB
    scoped_db = OrgScopedDB(db, req.org_id)

    chatid = req.chatid
    chattype = "single"
    if not chatid:
        user_svc = WecomUserMappingService(scoped_db)
        info = await user_svc.get_chatid_by_user_id(req.user_id)
        if not info:
            return {"success": False, "error": "未找到该用户的 chatid，请先让用户发送消息"}
        chatid = info["chatid"]
        chattype = info["chattype"]

    # 3. 发送
    ok = await ws_client.send_msg(
        chatid=chatid,
        msgtype=req.msgtype,
        content={"content": req.message},
        chattype=chattype,
    )

    return {"success": ok}
