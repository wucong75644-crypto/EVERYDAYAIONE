"""
认证路由

提供用户注册、登录、发送验证码等接口。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import CurrentUser, Database, OrgCtx
from schemas.auth import (
    CurrentMember,
    CurrentOrgInfo,
    LoginResponse,
    OrgLoginRequest,
    OrgLoginResponse,
    PasswordLoginRequest,
    PhoneLoginRequest,
    PhoneRegisterRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    UserResponse,
    VerifyCodeRequest,
)
from services.auth_service import AuthService
from services.permissions.effective_perms import (
    compute_user_permissions,
    get_member_context,
)

router = APIRouter(prefix="/auth", tags=["认证"])


def get_auth_service(db: Database) -> AuthService:
    """获取认证服务实例（认证路由为公开接口，使用无需登录的 Database）"""
    return AuthService(db)


@router.post("/send-code", summary="发送验证码")
async def send_verification_code(
    request: SendCodeRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    发送手机验证码

    - **phone**: 手机号
    - **purpose**: 验证码用途 (register/login/reset_password/bind_phone)
    """
    try:
        await auth_service.send_verification_code(request.phone, request.purpose)
        return {"message": "验证码已发送"}
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/verify-code", summary="验证验证码")
async def verify_code(
    request: VerifyCodeRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    验证验证码（用于忘记密码第一步）

    - **phone**: 手机号
    - **code**: 验证码
    - **purpose**: 验证码用途
    """
    try:
        await auth_service.verify_code_only(request.phone, request.code, request.purpose)
        return {"message": "验证成功"}
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/reset-password", summary="重置密码")
async def reset_password(
    request: ResetPasswordRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    重置密码（忘记密码第二步）

    - **phone**: 手机号
    - **code**: 验证码
    - **new_password**: 新密码（至少8位，包含字母和数字）
    """
    try:
        result = await auth_service.reset_password(
            phone=request.phone,
            code=request.code,
            new_password=request.new_password,
        )
        return result
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/register", response_model=LoginResponse, summary="手机号注册")
async def register_by_phone(
    request: PhoneRegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    手机号注册新用户

    - **phone**: 手机号
    - **code**: 验证码
    - **nickname**: 昵称（可选）
    - **password**: 密码（可选，设置后可使用密码登录）
    """
    try:
        result = await auth_service.register_by_phone(
            phone=request.phone,
            code=request.code,
            nickname=request.nickname,
            password=request.password,
        )
        return result
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/login/phone", response_model=LoginResponse, summary="验证码登录")
async def login_by_phone(
    request: PhoneLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    手机号验证码登录

    - **phone**: 手机号
    - **code**: 验证码
    """
    try:
        result = await auth_service.login_by_phone(
            phone=request.phone,
            code=request.code,
        )
        return result
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/login/password", response_model=LoginResponse, summary="密码登录")
async def login_by_password(
    request: PasswordLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    手机号密码登录

    - **phone**: 手机号
    - **password**: 密码
    """
    try:
        result = await auth_service.login_by_password(
            phone=request.phone,
            password=request.password,
        )
        return result
    except Exception as e:
        # 业务异常会被全局异常处理器捕获
        raise


@router.post("/login/org", response_model=OrgLoginResponse, summary="企业密码登录")
async def login_by_org(
    request: OrgLoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    企业密码登录

    - **org_name**: 企业全称（精确匹配）
    - **phone**: 手机号
    - **password**: 密码
    """
    result = await auth_service.login_by_org_password(
        org_name=request.org_name,
        phone=request.phone,
        password=request.password,
    )
    return result


@router.get("/me", response_model=UserResponse, summary="获取当前用户信息")
async def get_current_user_info(
    current_user: CurrentUser,
    org_ctx: OrgCtx,
    db: Database,
) -> dict:
    """
    获取当前登录用户的信息

    需要在 Header 中携带 Authorization: Bearer <token>
    可选 X-Org-Id Header：决定 current_org 字段返回哪个企业的上下文

    V1.0+ 扩展返回字段：
    - current_org: 当前组织 + 成员任职信息 + 扁平化权限码
    - orgs: 用户所属的所有组织列表（用于切换）
    """
    phone = current_user.get("phone")
    masked_phone = None
    if phone and len(phone) >= 7:
        masked_phone = f"{phone[:3]}****{phone[-4:]}"

    response: dict = {
        "id": current_user["id"],
        "nickname": current_user["nickname"],
        "avatar_url": current_user.get("avatar_url"),
        "phone": masked_phone,
        "role": current_user["role"],
        "credits": current_user["credits"],
        "created_at": current_user["created_at"],
        "current_org": None,
        "orgs": [],
    }

    # 当前组织信息（V1.0+ 扩展）
    # 优先用 X-Org-Id header（OrgCtx 已校验过成员资格），
    # 回退到 token payload 里的 current_org_id（向后兼容老 token）
    user_id = current_user["id"]
    current_org_id = org_ctx.org_id or current_user.get("current_org_id")

    if current_org_id:
        try:
            # 1. 查组织基本信息
            org_result = db.table("organizations") \
                .select("id, name") \
                .eq("id", current_org_id) \
                .single() \
                .execute()
            org_info = org_result.data if org_result and org_result.data else None

            # 2. 查 org_members.role（全局控制权限）
            member_result = db.table("org_members") \
                .select("role") \
                .eq("org_id", current_org_id) \
                .eq("user_id", user_id) \
                .limit(1) \
                .execute()
            org_member_role = (
                member_result.data[0]["role"]
                if member_result and member_result.data
                else "member"
            )

            # 3. 查任职信息（业务数据权限）
            member_ctx = await get_member_context(db, user_id, current_org_id)
            member_obj = None
            if member_ctx:
                member_obj = {
                    "position_code": member_ctx["position_code"],
                    "department_id": member_ctx.get("department_id"),
                    "department_name": member_ctx.get("department_name"),
                    "department_type": member_ctx.get("department_type"),
                    "job_title": member_ctx.get("job_title"),
                    "data_scope": member_ctx["data_scope"],
                    "managed_departments": member_ctx.get("managed_departments"),
                }

            # 4. 计算扁平化权限码
            permissions = await compute_user_permissions(db, user_id, current_org_id)

            if org_info:
                response["current_org"] = {
                    "id": org_info["id"],
                    "name": org_info["name"],
                    "role": org_member_role,
                    "member": member_obj,
                    "permissions": permissions,
                }
        except Exception:
            # 容错：组织信息查询失败不影响基础用户信息返回
            pass

    # 用户所属的所有组织列表（用于切换）
    try:
        orgs_result = db.table("org_members") \
            .select("org_id, role") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        org_ids = [row["org_id"] for row in (orgs_result.data or [])]
        if org_ids:
            orgs_meta = db.table("organizations") \
                .select("id, name") \
                .in_("id", org_ids) \
                .execute()
            id_to_name = {o["id"]: o["name"] for o in (orgs_meta.data or [])}
            response["orgs"] = [
                {
                    "id": row["org_id"],
                    "name": id_to_name.get(row["org_id"], ""),
                    "role": row["role"],
                }
                for row in (orgs_result.data or [])
                if row["org_id"] in id_to_name
            ]
    except Exception:
        pass

    return response


@router.post("/refresh", summary="刷新令牌")
async def refresh_token(
    req: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    """
    用 refresh token 换取新的 access token + refresh token（轮换模式）。

    - 旧 refresh token 立即失效
    - 如果检测到已吊销的 refresh token 被重用，自动吊销该用户所有 token（防盗用）
    """
    return await auth_service.refresh_access_token(req.refresh_token)


class _OptionalRefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/logout", summary="退出登录")
async def logout(
    req: _OptionalRefreshRequest = _OptionalRefreshRequest(),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """
    退出登录

    可选传入 refresh_token 用于服务端吊销。
    客户端同时需要清除本地存储的 token。
    """
    if req.refresh_token:
        from core.security import hash_refresh_token
        from datetime import datetime as _dt, timezone as _tz
        token_hash = hash_refresh_token(req.refresh_token)
        auth_service.db.table("refresh_tokens").update({
            "revoked": True,
            "revoked_at": _dt.now(_tz.utc).isoformat(),
        }).eq("token_hash", token_hash).execute()
    return {"message": "已退出登录"}
