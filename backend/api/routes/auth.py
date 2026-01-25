"""
认证路由

提供用户注册、登录、发送验证码等接口。
"""

from fastapi import APIRouter, Depends
from supabase import Client

from api.deps import CurrentUser, Database
from schemas.auth import (
    LoginResponse,
    PasswordLoginRequest,
    PhoneLoginRequest,
    PhoneRegisterRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    UserResponse,
    VerifyCodeRequest,
)
from services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


def get_auth_service(db: Database) -> AuthService:
    """获取认证服务实例"""
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
    await auth_service.send_verification_code(request.phone, request.purpose)
    return {"message": "验证码已发送"}


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
    await auth_service.verify_code_only(request.phone, request.code, request.purpose)
    return {"message": "验证成功"}


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
    result = await auth_service.reset_password(
        phone=request.phone,
        code=request.code,
        new_password=request.new_password,
    )
    return result


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
    result = await auth_service.register_by_phone(
        phone=request.phone,
        code=request.code,
        nickname=request.nickname,
        password=request.password,
    )
    return result


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
    result = await auth_service.login_by_phone(
        phone=request.phone,
        code=request.code,
    )
    return result


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
    result = await auth_service.login_by_password(
        phone=request.phone,
        password=request.password,
    )
    return result


@router.get("/me", response_model=UserResponse, summary="获取当前用户信息")
async def get_current_user_info(current_user: CurrentUser) -> dict:
    """
    获取当前登录用户的信息

    需要在 Header 中携带 Authorization: Bearer <token>
    """
    phone = current_user.get("phone")
    masked_phone = None
    if phone and len(phone) >= 7:
        masked_phone = f"{phone[:3]}****{phone[-4:]}"

    return {
        "id": current_user["id"],
        "nickname": current_user["nickname"],
        "avatar_url": current_user.get("avatar_url"),
        "phone": masked_phone,
        "role": current_user["role"],
        "credits": current_user["credits"],
        "created_at": current_user["created_at"],
    }


@router.post("/logout", summary="退出登录")
async def logout() -> dict:
    """
    退出登录

    客户端需要清除本地存储的 token
    """
    return {"message": "已退出登录"}
