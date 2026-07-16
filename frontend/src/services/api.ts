/**
 * API 请求基础配置
 *
 * 401 处理流程（双 Token 无感刷新）：
 * 1. 收到 401 → 调 silentRefresh() 尝试用 refresh_token 换新 token
 * 2. 刷新成功 → 用新 token 重发原请求（用户无感知）
 * 3. 刷新失败 → logoutOnce() 统一登出
 * 4. 并发 401 → 第一个触发刷新，其余排队等新 token 后重发
 *
 * 429 处理流程（任务限流统一弹 toast）：
 * 1. 后端 task_limit_service 是任务计数单一事实来源,超限返回 429
 * 2. 收到 429 → 解析 ApiErrorResponse.message → toast.error
 * 3. 不重试(限流不是临时错误)
 */

import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios';
import toast from 'react-hot-toast';
import type { ApiErrorResponse } from '../types/auth';
import { silentRefresh } from '../utils/tokenManager';

// 优先使用环境变量，默认使用相对路径（适用于同域名部署）
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export class ApiRequestError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
    public readonly details?: Record<string, unknown>,
    public readonly transport: 'http' | 'timeout' | 'network' = 'http',
    public readonly sendDisposition?: 'rejected' | 'recorded_failure' | 'uncertain',
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }

  get retryAfterMs(): number | undefined {
    const seconds = Number(this.details?.retry_after);
    return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : undefined;
  }
}

export function toApiRequestError(error: unknown): ApiRequestError {
  if (error instanceof ApiRequestError) return error;
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    const apiError = error.response?.data?.error;
    if (apiError?.message) {
      return new ApiRequestError(
        apiError.code || 'API_ERROR', apiError.message,
        error.response?.status, apiError.details, 'http',
      );
    }
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
      return new ApiRequestError('REQUEST_TIMEOUT', '请求超时', undefined, undefined, 'timeout');
    }
    if (!error.response) {
      return new ApiRequestError(
        'NETWORK_ERROR', error.message || '网络请求失败', undefined, undefined, 'network',
      );
    }
    return new ApiRequestError(
      'API_ERROR', error.message || '请求失败', error.response.status, undefined, 'http',
    );
  }
  return new ApiRequestError(
    'NETWORK_ERROR', error instanceof Error ? error.message : '网络请求失败',
    undefined, undefined, 'network',
  );
}

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

    // 429 任务限流:后端 task_limit_service 返回 TASK_QUEUE_FULL,统一弹 toast
    if (error.response?.status === 429) {
      const message = error.response?.data?.error?.message || '请求过于频繁，请稍后再试';
      toast.error(message);
    }

    // 其他错误 → 直接 reject(由调用方处理)
    return Promise.reject(error);
  }
);

/**
 * 通用请求方法
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await api.request<T>(config);
    return response.data;
  } catch (error) {
    throw toApiRequestError(error);
  }
}

export default api;
