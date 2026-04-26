"""
认证相关的请求/响应模型

定义登录、注册等接口的数据结构。
"""

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PhoneLoginRequest(BaseModel):
    """手机号登录请求"""

    phone: str = Field(..., description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="验证码")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class PhoneRegisterRequest(BaseModel):
    """手机号注册请求"""

    phone: str = Field(..., description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="验证码")
    nickname: Optional[str] = Field(None, max_length=50, description="昵称")
    password: Optional[str] = Field(None, min_length=6, max_length=32, description="密码")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class PasswordLoginRequest(BaseModel):
    """密码登录请求"""

    phone: str = Field(..., description="手机号")
    password: str = Field(..., min_length=6, max_length=32, description="密码")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class SendCodeRequest(BaseModel):
    """发送验证码请求"""

    phone: str = Field(..., description="手机号")
    purpose: Literal["register", "login", "reset_password", "bind_phone"] = Field(
        ..., description="验证码用途"
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class TokenResponse(BaseModel):
    """Token 响应"""

    access_token: str = Field(..., description="访问令牌")
    refresh_token: str = Field(..., description="刷新令牌（用于无感续期）")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(..., description="access_token 过期时间（秒）")
    refresh_expires_in: int = Field(..., description="refresh_token 过期时间（秒）")


class CurrentMember(BaseModel):
    """当前组织内的成员任职信息（V1.0+ 新增）"""

    position_code: Literal["boss", "vp", "manager", "deputy", "member"] = Field(..., description="职位代码")
    department_id: Optional[str] = Field(None, description="主部门 ID")
    department_name: Optional[str] = Field(None, description="主部门名称")
    department_type: Optional[Literal["ops", "finance", "warehouse", "service", "design", "hr", "other"]] = Field(None, description="部门类型")
    job_title: Optional[str] = Field(None, description="自定义头衔")
    data_scope: Literal["all", "dept_subtree", "self"] = Field(..., description="数据范围")
    managed_departments: Optional[List[Dict[str, str]]] = Field(None, description="副总分管的部门列表 [{id, name}]")


class CurrentOrgInfo(BaseModel):
    """当前组织信息（V1.0+ 扩展）"""

    id: str = Field(..., description="组织 ID")
    name: str = Field(..., description="组织名称")
    role: Literal["owner", "admin", "member"] = Field(..., description="组织内角色（org_members.role，全局控制）")
    member: Optional[CurrentMember] = Field(None, description="成员任职信息（业务数据权限）")
    permissions: List[str] = Field(default_factory=list, description="扁平化权限码列表")


class UserResponse(BaseModel):
    """用户信息响应"""

    id: str = Field(..., description="用户ID")
    nickname: str = Field(..., description="昵称")
    avatar_url: Optional[str] = Field(None, description="头像URL")
    phone: Optional[str] = Field(None, description="手机号（脱敏）")
    role: str = Field(..., description="用户角色")
    credits: int = Field(..., description="积分余额")
    created_at: str = Field(..., description="注册时间")

    # 新增字段（V1.0+）
    current_org: Optional[CurrentOrgInfo] = Field(None, description="当前组织信息（含成员任职 + 权限码）")
    orgs: List[Dict[str, Any]] = Field(default_factory=list, description="所属组织列表 [{id, name, role}]，用于切换组织")


class LoginResponse(BaseModel):
    """登录响应"""

    token: TokenResponse = Field(..., description="令牌信息")
    user: UserResponse = Field(..., description="用户信息")


class RefreshTokenRequest(BaseModel):
    """刷新令牌请求"""

    refresh_token: str = Field(..., description="刷新令牌")


class VerifyCodeRequest(BaseModel):
    """验证码验证请求"""

    phone: str = Field(..., description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="验证码")
    purpose: Literal["register", "login", "reset_password", "bind_phone"] = Field(
        ..., description="验证码用途"
    )

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class ResetPasswordRequest(BaseModel):
    """重置密码请求"""

    phone: str = Field(..., description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="验证码")
    new_password: str = Field(..., min_length=8, max_length=32, description="新密码")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """验证手机号格式"""
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """验证密码强度：至少包含字母和数字"""
        if not re.search(r"[a-zA-Z]", v):
            raise ValueError("密码必须包含字母")
        if not re.search(r"\d", v):
            raise ValueError("密码必须包含数字")
        return v


class OrgLoginRequest(BaseModel):
    """企业密码登录请求"""

    org_name: str = Field(..., min_length=1, max_length=100, description="企业全称（精确匹配）")
    phone: str = Field(..., description="手机号")
    password: str = Field(..., min_length=6, max_length=32, description="密码")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        phone_pattern = re.compile(r"^1[3-9]\d{9}$")
        if not phone_pattern.match(v):
            raise ValueError("手机号格式不正确")
        return v


class OrgInfo(BaseModel):
    """企业信息（登录响应中携带）"""

    org_id: str = Field(..., description="企业ID")
    org_name: str = Field(..., description="企业名称")
    org_role: str = Field(..., description="成员角色")


class OrgLoginResponse(BaseModel):
    """企业登录响应"""

    token: TokenResponse = Field(..., description="令牌信息")
    user: UserResponse = Field(..., description="用户信息")
    org: OrgInfo = Field(..., description="企业信息")
