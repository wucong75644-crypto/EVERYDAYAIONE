"""WebSocket 握手认证与可观察拒绝语义。"""

from typing import Optional

from fastapi import WebSocket
from loguru import logger

from core.exceptions import TokenExpiredError
from core.security import decode_access_token


async def reject_websocket(
    websocket: WebSocket,
    *,
    code: int,
    reason: str,
) -> None:
    """完成握手后返回浏览器可观察的 WebSocket 关闭码。"""
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


async def get_user_from_token(token: str) -> tuple[Optional[str], str]:
    """解析访问令牌，返回用户 ID 和稳定的失败类型。"""
    try:
        payload = decode_access_token(token)
        return payload.get("sub"), ""
    except TokenExpiredError:
        logger.debug("Token expired")
        return None, "expired"
    except Exception as error:
        logger.warning(f"Token invalid | error={error}")
        return None, "invalid"
