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

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface ApiErrorResponse {
  error: ApiError;
}
