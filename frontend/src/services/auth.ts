/**
 * 认证相关 API
 */

import { request } from './api';
import type {
  LoginResponse,
  PasswordLoginRequest,
  PhoneLoginRequest,
  PhoneRegisterRequest,
  SendCodeRequest,
  User,
} from '../types/auth';

/**
 * 发送验证码
 */
export async function sendCode(data: SendCodeRequest): Promise<{ message: string }> {
  return request({
    method: 'POST',
    url: '/auth/send-code',
    data,
  });
}

/**
 * 手机号验证码登录
 */
export async function loginByPhone(data: PhoneLoginRequest): Promise<LoginResponse> {
  return request({
    method: 'POST',
    url: '/auth/login/phone',
    data,
  });
}

/**
 * 密码登录
 */
export async function loginByPassword(data: PasswordLoginRequest): Promise<LoginResponse> {
  return request({
    method: 'POST',
    url: '/auth/login/password',
    data,
  });
}

/**
 * 手机号注册
 */
export async function register(data: PhoneRegisterRequest): Promise<LoginResponse> {
  return request({
    method: 'POST',
    url: '/auth/register',
    data,
  });
}

/**
 * 获取当前用户信息
 */
export async function getCurrentUser(): Promise<User> {
  return request({
    method: 'GET',
    url: '/auth/me',
  });
}

/**
 * 退出登录
 */
export async function logout(): Promise<{ message: string }> {
  return request({
    method: 'POST',
    url: '/auth/logout',
  });
}
