"""
企业微信自建应用回调路由

- GET  /api/wecom/callback/{org_id} — 企业级 URL 验证
- POST /api/wecom/callback/{org_id} — 验签解密并持久化入队
"""

import asyncio
import hashlib
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

from api.deps import CurrentUserId, Database, OrgCtx
from services.wecom.crypto import WXBizMsgCrypt

router = APIRouter(prefix="/wecom", tags=["企业微信回调"])


def _get_crypt(org_id: str) -> tuple[WXBizMsgCrypt, str]:
    """获取指定企业的回调加解密器和 CorpID。"""
    from core.database import get_worker_db
    from services.wecom.callback_config import resolve_wecom_callback_config

    config = resolve_wecom_callback_config(get_worker_db(), org_id)
    return WXBizMsgCrypt(
        token=config.token,
        encoding_aes_key=config.encoding_aes_key,
        corp_id=config.corp_id,
    ), config.corp_id


@router.get("/callback/{org_id}", summary="URL 验证")
async def verify_url(
    org_id: str,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> PlainTextResponse:
    """
    企微配置回调 URL 时发送的验证请求。

    流程：验签 → 解密 echostr → 返回明文。
    """
    crypt, _corp_id = await asyncio.to_thread(_get_crypt, org_id)
    ret, decrypted = crypt.verify_url(msg_signature, timestamp, nonce, echostr)

    if ret != 0:
        logger.warning(f"Wecom callback: URL verify failed | ret={ret}")
        return PlainTextResponse("verify failed", status_code=403)

    logger.info("Wecom callback: URL verified OK")
    return PlainTextResponse(decrypted)


# TODO(time-context PR3): receive_message + _process_callback_xml 注入 RequestContext
# 目前 ERPAgent 内部用 RequestContext.build() fallback，时区正确但失去"请求级 SSOT"。
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B13/B14)
@router.post("/callback/{org_id}", summary="接收消息")
async def receive_message(
    org_id: str,
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

    crypt, corp_id = await asyncio.to_thread(_get_crypt, org_id)
    ret, xml_content = crypt.decrypt_msg(
        post_data, msg_signature, timestamp, nonce,
    )

    if ret != 0:
        logger.warning(f"Wecom callback: decrypt failed | ret={ret}")
        return PlainTextResponse("decrypt failed", status_code=403)

    root = ET.fromstring(xml_content)
    message_key = (
        _xml_text(root, "MsgId")
        or _xml_text(root, "NewMsgId")
        or hashlib.sha256(xml_content.encode("utf-8")).hexdigest()
    )
    db.rpc("enqueue_wecom_callback", {
        "p_org_id": org_id,
        "p_corp_id": corp_id,
        "p_message_key": message_key,
        "p_payload": {"xml_content": xml_content},
    }).execute()

    # 立即返回（5 秒限制）
    return PlainTextResponse("success")


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
async def push_message(
    req: WecomPushRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    db: Database,
) -> dict:
    """主动推送消息到企微用户（内部调用）

    通过 WS 长连接的 aibot_send_msg 向指定用户发送消息。
    """
    if not org_ctx.org_id or req.org_id != org_ctx.org_id:
        raise HTTPException(status_code=403, detail="企微推送企业范围不匹配")
    if req.msgtype not in ("text", "markdown"):
        raise HTTPException(status_code=400, detail="企微推送消息类型无效")
    try:
        result = db.rpc("resolve_governed_wecom_push_target", {
            "p_org_id": org_ctx.org_id,
            "p_target_user_id": req.user_id,
            "p_chatid": req.chatid,
        }).execute()
    except Exception as exc:
        if "GOVERNANCE_AUTHORITY_DENIED" in str(exc):
            raise HTTPException(
                status_code=403, detail="仅老板/管理员可主动推送企微消息",
            ) from exc
        raise
    target = result.data
    if not isinstance(target, dict) or not target.get("chatid"):
        return {"success": False, "error": "未找到企业内有效的企微推送目标"}

    from services.scheduler.push_dispatcher import push_dispatcher
    ok = await push_dispatcher.publish_wecom_message(
        org_id=org_ctx.org_id,
        chatid=target["chatid"],
        msgtype=req.msgtype,
        content={"content": req.message},
    )
    return {"success": ok}
