/**
 * API 请求基础配置
 */

import axios, { AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios';
import type { ApiErrorResponse } from '../types/auth';

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

// 请求拦截器：添加 token + 企业上下文
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
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：处理错误
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorResponse>) => {
    if (error.response?.status === 401) {
      // Token 过期或无效，清除本地存储（含企业上下文）
      // 优先用 login_org_id，兜底用 current_org_id（老版本登录的企业用户可能没有 login_org_id）
      const loginOrgId = localStorage.getItem('login_org_id') || localStorage.getItem('current_org_id');
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      localStorage.removeItem('current_org_id');
      localStorage.removeItem('current_org');
      // 只在非首页时跳转，避免首页循环重定向
      if (window.location.pathname !== '/') {
        // 企业用户跳回企业登录页，散客跳回首页
        window.location.href = loginOrgId ? `/?org=${loginOrgId}` : '/';
      }
    }
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
