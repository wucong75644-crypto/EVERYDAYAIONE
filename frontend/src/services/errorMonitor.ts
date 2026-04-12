/**
 * 系统错误监控 API 服务
 */

import { request } from './api';

// ── 类型定义 ─────────────────────────────────────────────

export interface ErrorLogItem {
  id: number;
  fingerprint: string;
  level: string;
  module: string | null;
  function: string | null;
  line: number | null;
  message: string;
  traceback: string | null;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  org_id: string | null;
  is_critical: boolean;
  is_resolved: boolean;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface ErrorListResponse {
  items: ErrorLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ErrorStatsResponse {
  today_total: number;
  today_critical: number;
  week_total: number;
  unresolved: number;
  top_modules: { module: string; count: number }[];
}

export interface SummarizeResponse {
  summary: string;
}

// ── 查询参数 ─────────────────────────────────────────────

export interface ErrorListParams {
  page?: number;
  page_size?: number;
  level?: string;
  is_critical?: boolean;
  is_resolved?: boolean;
  search?: string;
  days?: number;
}

// ── API 函数 ─────────────────────────────────────────────

export async function listErrors(params: ErrorListParams = {}): Promise<ErrorListResponse> {
  return request({ method: 'GET', url: '/error-monitor/list', params });
}

export async function getErrorStats(): Promise<ErrorStatsResponse> {
  return request({ method: 'GET', url: '/error-monitor/stats' });
}

export async function summarizeErrors(days: number = 7): Promise<SummarizeResponse> {
  return request({ method: 'POST', url: '/error-monitor/summarize', params: { days } });
}

export async function resolveError(errorId: number): Promise<{ success: boolean }> {
  return request({ method: 'POST', url: `/error-monitor/${errorId}/resolve` });
}

export async function clearErrors(
  resolvedOnly: boolean = true,
  beforeDate?: string,
): Promise<{ success: boolean; deleted: number }> {
  return request({
    method: 'DELETE',
    url: '/error-monitor/clear',
    params: { resolved_only: resolvedOnly, before_date: beforeDate },
  });
}
