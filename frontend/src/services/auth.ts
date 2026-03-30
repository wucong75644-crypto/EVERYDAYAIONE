/**
 * 认证相关 API
 */

import { request } from './api';
import type {
  LoginResponse,
  Organization,
  OrgLoginRequest,
  OrgLoginResponse,
  PasswordLoginRequest,
  PhoneLoginRequest,
  PhoneRegisterRequest,
  SendCodeRequest,
  User,
  WecomQrUrlResponse,
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
 * 获取企微扫码登录 URL
 */
export async function getWecomQrUrl(): Promise<WecomQrUrlResponse> {
  return request({
    method: 'GET',
    url: '/auth/wecom/qr-url',
  });
}

/**
 * 查询企微绑定状态
 */
export async function getWecomBindingStatus(): Promise<{
  bound: boolean;
  wecom_nickname: string | null;
  bound_at: string | null;
}> {
  return request({
    method: 'GET',
    url: '/auth/wecom/binding-status',
  });
}

/**
 * 解绑企微账号
 */
export async function unbindWecom(): Promise<{ success: boolean; message: string }> {
  return request({
    method: 'DELETE',
    url: '/auth/wecom/binding',
  });
}

/**
 * 企业密码登录
 */
export async function loginByOrg(data: OrgLoginRequest): Promise<OrgLoginResponse> {
  return request({
    method: 'POST',
    url: '/auth/login/org',
    data,
  });
}

/**
 * 获取当前用户的企业列表
 */
export async function listMyOrganizations(): Promise<Organization[]> {
  return request({
    method: 'GET',
    url: '/org',
  });
}

