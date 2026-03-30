/**
 * 认证相关类型定义
 */

export interface User {
  id: string;
  nickname: string;
  avatar_url: string | null;
  phone: string | null;
  role: 'user' | 'admin' | 'super_admin';
  credits: number;
  created_at: string;
  wecom_bound?: boolean;
}

export interface WecomQrUrlResponse {
  qr_url: string;
  state: string;
  appid: string;
  agentid: string;
  redirect_uri: string;
}

export interface TokenInfo {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginResponse {
  token: TokenInfo;
  user: User;
}

export interface SendCodeRequest {
  phone: string;
  purpose: 'register' | 'login' | 'reset_password' | 'bind_phone';
}

export interface PhoneLoginRequest {
  phone: string;
  code: string;
}

export interface PhoneRegisterRequest {
  phone: string;
  code: string;
  nickname: string;
  password: string;
}

export interface PasswordLoginRequest {
  phone: string;
  password: string;
}

// ── 企业相关 ──

export interface Organization {
  org_id: string;
  name: string;
  role: 'owner' | 'admin' | 'member';
  logo_url?: string | null;
  features?: Record<string, boolean>;
}

export interface OrgLoginRequest {
  org_name: string;
  phone: string;
  password: string;
}

export interface OrgLoginResponse {
  token: TokenInfo;
  user: User;
  org: {
    org_id: string;
    org_name: string;
    org_role: string;
  };
}

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface ApiErrorResponse {
  error: ApiError;
}
