"""
拼多多开放平台回调路由

拼多多应用审核要求必须提供可访问的回调地址。
当前项目通过快麦ERP间接对接拼多多，暂不直接消费PDD回调事件，
此路由仅作为审核必需的合规端点。
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

router = APIRouter(prefix="/pdd", tags=["拼多多回调"])


@router.post("/callback", summary="拼多多消息回调")
async def pdd_callback(request: Request) -> JSONResponse:
    """接收拼多多开放平台推送的消息通知"""
    body = await request.body()
    logger.info(f"PDD callback received: {body[:500]}")
    return JSONResponse(content={"success": True})


@router.get("/callback", summary="拼多多回调地址验证")
async def pdd_callback_verify(request: Request) -> JSONResponse:
    """拼多多验证回调地址可达性（GET 请求）"""
    return JSONResponse(content={"success": True})
