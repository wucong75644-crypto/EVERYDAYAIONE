"""
FastAPI 依赖注入

提供通用的依赖注入函数，如获取当前用户、数据库连接等。
"""

from dataclasses import dataclass
from typing import Annotated, Any, Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from loguru import logger

from core.database import get_db
from core.exceptions import AuthenticationError, InvalidTokenError
from core.org_scoped_db import OrgScopedDB
from core.security import decode_access_token
from core.redis import RedisClient
from services.task_limit_service import TaskLimitService


async def get_current_user_id(
    authorization: Annotated[Optional[str], Header()] = None,
) -> str:
    """
    从 Authorization Header 中获取当前用户 ID

    Args:
        authorization: Authorization Header，格式为 "Bearer <token>"

    Returns:
        用户 ID

    Raises:
        AuthenticationError: 未提供认证信息
        InvalidTokenError: Token 无效
    """
    if not authorization:
        raise AuthenticationError("请先登录")

    if not authorization.startswith("Bearer "):
        raise AuthenticationError("认证格式错误")

    token = authorization[7:]  # 去掉 "Bearer " 前缀

    payload = decode_access_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise InvalidTokenError()

    return user_id


async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
    db: Annotated[Any, Depends(get_db)],
) -> dict:
    """
    获取当前登录用户的完整信息

    Args:
        user_id: 用户 ID
        db: 数据库客户端

    Returns:
        用户信息字典

    Raises:
        AuthenticationError: 用户不存在
    """
    response = db.table("users").select(
        "id, phone, nickname, avatar_url, role, credits, status, created_at"
    ).eq("id", user_id).single().execute()

    if not response.data:
        raise AuthenticationError("用户不存在")

    return response.data


async def get_optional_user_id(
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[str]:
    """
    可选的用户认证，未登录时返回 None

    用于同时支持登录和未登录用户的接口。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization[7:]
        payload = decode_access_token(token)
        return payload.get("sub")
    except Exception:
        return None


async def get_task_limit_service() -> Optional[TaskLimitService]:
    """
    获取任务限制服务实例

    如果 Redis 不可用，返回 None（降级处理）
    """
    try:
        redis_client = await RedisClient.get_client()
        return TaskLimitService(redis_client)
    except Exception as e:
        logger.warning(f"TaskLimitService 初始化失败，降级跳过限制 | error={e}")
        return None


# ── Org 上下文 ─────────────────────────────────────────────


@dataclass
class OrgContext:
    """企业上下文，由 X-Org-Id Header 决定"""

    user_id: str
    org_id: str | None = None       # None = 散客
    org_role: str | None = None     # owner / admin / member / None


async def get_org_context(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: Any = Depends(get_db),
) -> OrgContext:
    """
    从 X-Org-Id Header 解析企业上下文。

    - 无 Header → 散客模式（org_id=None）
    - 有 Header → 校验 UUID 格式 → 校验企业状态 → 校验成员资格
    """
    raw_org_id = request.headers.get("X-Org-Id")
    if not raw_org_id:
        return OrgContext(user_id=user_id)

    # 校验 UUID 格式
    try:
        UUID(raw_org_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Org-Id 格式无效")

    # 校验企业存在且活跃（统一错误信息防枚举）
    _deny = HTTPException(status_code=403, detail="无权访问该企业")

    org_result = (
        db.table("organizations")
        .select("status")
        .eq("id", raw_org_id)
        .maybe_single()
        .execute()
    )
    if not org_result or not org_result.data:
        raise _deny
    if org_result.data["status"] != "active":
        raise _deny

    # 校验用户是该企业的有效成员
    member = (
        db.table("org_members")
        .select("role, status")
        .eq("org_id", raw_org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not member or not member.data:
        raise _deny
    if member.data["status"] != "active":
        raise _deny

    return OrgContext(
        user_id=user_id,
        org_id=raw_org_id,
        org_role=member.data["role"],
    )


# ── 多租户隔离 DB ────────────────────────────────────────


async def get_scoped_db(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: Any = Depends(get_db),
) -> OrgScopedDB:
    """
    构造租户隔离的数据库客户端。

    从 X-Org-Id Header 解析 org_id，包装 raw db 为 OrgScopedDB。
    TENANT_TABLES 中的表查询自动注入 org_id 过滤。

    注意：不重复校验 org_members（OrgCtx 已校验过），仅提取 org_id。
    """
    raw_org_id = request.headers.get("X-Org-Id")
    org_id: str | None = None
    if raw_org_id:
        try:
            UUID(raw_org_id)
            org_id = raw_org_id
        except ValueError:
            pass  # 格式无效时当散客处理，OrgCtx 会拦截
    return OrgScopedDB(db, org_id)


# ── 时间事实层 RequestContext ────────────────────────────
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B11)
# 在 HTTP 入口构造一次，全链路传递，禁止下游重新计算 now（避免跨午夜漂移）。


async def get_request_ctx(
    request: Request,
    org_ctx: OrgContext = Depends(get_org_context),
):
    """构造请求级 RequestContext（含 now / weekday / iso_week / 节假日）。

    包装 OrgContext + 时间事实，是 HTTP/WebSocket/企微入口的统一注入方式。
    """
    from utils.time_context import RequestContext
    return RequestContext.build(
        user_id=org_ctx.user_id,
        org_id=org_ctx.org_id,
        request_id=request.headers.get("X-Request-Id", ""),
    )


# 类型别名，简化依赖注入的使用
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
OptionalUserId = Annotated[Optional[str], Depends(get_optional_user_id)]
Database = Annotated[Any, Depends(get_db)]
OrgCtx = Annotated[OrgContext, Depends(get_org_context)]
ScopedDB = Annotated[OrgScopedDB, Depends(get_scoped_db)]
TaskLimitSvc = Annotated[Optional[TaskLimitService], Depends(get_task_limit_service)]
# 时间事实层依赖 — 用法：def endpoint(req_ctx: RequestCtx, ...)
RequestCtx = Annotated["Any", Depends(get_request_ctx)]
