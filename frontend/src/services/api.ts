/**
 * API 请求基础配置
 *
 * 401 处理流程（双 Token 无感刷新）：
 * 1. 收到 401 → 调 silentRefresh() 尝试用 refresh_token 换新 token
 * 2. 刷新成功 → 用新 token 重发原请求（用户无感知）
 * 3. 刷新失败 → logoutOnce() 统一登出
 * 4. 并发 401 → 第一个触发刷新，其余排队等新 token 后重发
 */

import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios';
import type { ApiErrorResponse } from '../types/auth';
import { silentRefresh } from '../utils/tokenManager';

// 优先使用环境变量，默认使用相对路径（适用于同域名部署）
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// 创建 axios 实例
const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器：添加 token + 企业上下文 + FormData Content-Type 修正
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    const orgId = localStorage.getItem('current_org_id');
    if (orgId) {
      config.headers['X-Org-Id'] = orgId;
    }
    // FormData 时删除 Content-Type，让浏览器自动设置含 boundary 的 multipart header
    if (config.data instanceof FormData) {
      delete config.headers['Content-Type'];
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：401 → silentRefresh → 重发 / 登出
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorResponse>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        const newToken = await silentRefresh();
        // 用新 token 重发原请求
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      } catch {
        // silentRefresh 内部已调 logoutOnce()，这里只需 reject
        return Promise.reject(error);
      }
    }

    // 非 401 或已重试过 → 直接 reject
    return Promise.reject(error);
  }
);

/**
 * 通用请求方法
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await api.request<T>(config);
  return response.data;
}

export default api;
