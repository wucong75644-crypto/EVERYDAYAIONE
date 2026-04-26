/**
 * Token 管理器 — 双 Token 无感刷新核心
 *
 * 职责：
 * 1. silentRefresh()：用 refresh_token 换新 access + refresh token
 * 2. 刷新锁：防止并发 401 触发多次 refresh（thundering herd）
 * 3. 请求队列：refresh 期间的 401 请求排队，成功后批量重发
 * 4. logoutOnce()：统一登出入口，防重复跳转
 */

import axios from 'axios';
import { API_BASE_URL } from '../services/api';
import { useAuthStore } from '../stores/useAuthStore';
import { logger } from './logger';

// ── 刷新锁 + 等待队列 ────────────────────────────────────

let isRefreshing = false;
let refreshSubscribers: Array<(token: string) => void> = [];
let refreshRejectSubscribers: Array<(err: Error) => void> = [];

function onRefreshed(newToken: string) {
  refreshSubscribers.forEach((cb) => cb(newToken));
  refreshSubscribers = [];
  refreshRejectSubscribers = [];
}

function onRefreshFailed(err: Error) {
  refreshRejectSubscribers.forEach((cb) => cb(err));
  refreshSubscribers = [];
  refreshRejectSubscribers = [];
}

/**
 * 订阅 refresh 结果。返回 Promise<新 access_token>。
 * 如果 refresh 成功，resolve；失败则 reject。
 */
function subscribeTokenRefresh(): Promise<string> {
  return new Promise((resolve, reject) => {
    refreshSubscribers.push(resolve);
    refreshRejectSubscribers.push(reject);
  });
}

// ── 静默刷新 ────────────────────────────────────────────

/**
 * 用 refresh_token 换取新的 access + refresh token。
 *
 * - 并发安全：第一个 401 触发刷新，后续请求排队
 * - 成功后更新 localStorage + Zustand，批量通知队列
 * - 失败则统一登出
 *
 * @returns 新的 access_token（或抛错触发登出）
 */
export async function silentRefresh(): Promise<string> {
  // 已有刷新在进行 → 排队等待
  if (isRefreshing) {
    return subscribeTokenRefresh();
  }

  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) {
    logoutOnce();
    throw new Error('No refresh token');
  }

  isRefreshing = true;

  try {
    // 直接用 axios 发请求（绕过拦截器，避免死循环）
    const resp = await axios.post(
      `${API_BASE_URL}/auth/refresh`,
      { refresh_token: refreshToken },
      { timeout: 10000 },
    );

    const { token } = resp.data;
    const newAccessToken: string = token.access_token;
    const newRefreshToken: string = token.refresh_token;

    // 更新存储
    localStorage.setItem('access_token', newAccessToken);
    localStorage.setItem('refresh_token', newRefreshToken);

    // 同步 Zustand（如果 store 已初始化）
    try {
      useAuthStore.getState().setToken(newAccessToken);
    } catch {
      // store 未初始化时忽略
    }

    logger.info('auth:refresh', 'Token refreshed silently');

    // 通知队列中的请求
    onRefreshed(newAccessToken);

    return newAccessToken;
  } catch (err) {
    logger.warn('auth:refresh', 'Refresh failed, logging out', err);
    onRefreshFailed(err instanceof Error ? err : new Error('Refresh failed'));
    logoutOnce();
    throw err;
  } finally {
    isRefreshing = false;
  }
}

// ── 统一登出（防重复） ──────────────────────────────────

let isLoggingOut = false;

/**
 * 统一登出入口。
 *
 * - 清除 Zustand + localStorage
 * - 跳转首页（企业用户带 org 参数）
 * - 防抖：并发 401 只执行一次
 */
export function logoutOnce() {
  if (isLoggingOut) return;
  isLoggingOut = true;

  const loginOrgId =
    localStorage.getItem('login_org_id') ||
    localStorage.getItem('current_org_id');

  // 先更新 Zustand 状态（触发 UI 响应）
  try {
    useAuthStore.getState().clearAuth();
  } catch {
    // store 未初始化时手动清 localStorage
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    localStorage.removeItem('current_org_id');
    localStorage.removeItem('current_org');
  }

  // 跳转（非首页时才跳，避免循环）
  if (window.location.pathname !== '/') {
    window.location.href = loginOrgId ? `/?org=${loginOrgId}` : '/';
  }

  // 300ms 后重置锁（允许下次登出）
  setTimeout(() => {
    isLoggingOut = false;
  }, 300);
}

// ── Token 存取工具 ──────────────────────────────────────

export function getAccessToken(): string | null {
  return localStorage.getItem('access_token');
}

export function getRefreshToken(): string | null {
  return localStorage.getItem('refresh_token');
}

export function setTokens(accessToken: string, refreshToken: string) {
  localStorage.setItem('access_token', accessToken);
  localStorage.setItem('refresh_token', refreshToken);
}

export function clearTokens() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
}
