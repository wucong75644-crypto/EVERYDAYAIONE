/**
 * 快麦 Web 数据接入 — 前端 API 客户端
 *
 * 对应后端 backend/api/routes/kuaimai_external.py
 */

import { request } from './api';

export type KuaimaiSource = 'thinktank' | 'viperp';
export type SyncType = 'daily' | 'manual' | 'backfill';
export type CredentialStatus = 'active' | 'expired' | 'invalid';

// ── 类型 ──────────────────────────────────────────────────────

export interface Credential {
  id: string;
  source: KuaimaiSource;
  kuaimai_company_id: number;
  status: CredentialStatus;
  censeid_preview: string;
  last_health_check_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateCredentialResp {
  credential: Credential;
  detected_source: string;
  detected_companyid: number;
}

export interface SyncResult {
  success: boolean;
  log_id: string | null;
  rows_synced: number;
  cookie_expired: boolean;
  error: string | null;
  summary: Record<string, unknown> | null;
}

export interface SyncLog {
  id: string;
  source: string;
  sync_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  date_range_start: string | null;
  date_range_end: string | null;
  rows_synced: number;
  error_message: string | null;
  metadata: Record<string, unknown> | null;
}

export interface Operator {
  id: string;
  operator_name: string;
  wecom_userid: string | null;
  is_bound: boolean;
  is_active: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  bound_at: string | null;
  notes: string | null;
  shop_count: number;
}

export interface TestResult {
  ok: boolean;
  message: string;
}

// ── 凭证 ──────────────────────────────────────────────────────

export async function listCredentials(): Promise<Credential[]> {
  return request({ method: 'GET', url: '/admin/kuaimai/credentials' });
}

export async function createCredential(
  curlText: string,
  source?: KuaimaiSource,
): Promise<CreateCredentialResp> {
  return request({
    method: 'POST',
    url: '/admin/kuaimai/credentials',
    data: { curl_text: curlText, source },
  });
}

export async function deleteCredential(credentialId: string): Promise<{ deleted: boolean }> {
  return request({
    method: 'DELETE',
    url: `/admin/kuaimai/credentials/${credentialId}`,
  });
}

export async function testCredential(credentialId: string): Promise<TestResult> {
  return request({
    method: 'POST',
    url: `/admin/kuaimai/credentials/${credentialId}/test`,
  });
}

// ── 同步 ──────────────────────────────────────────────────────

export async function triggerSync(
  source: KuaimaiSource,
  options?: {
    sync_type?: SyncType;
    start_date?: string;
    end_date?: string;
    dimension?: string;
  },
): Promise<SyncResult> {
  return request({
    method: 'POST',
    url: `/admin/kuaimai/sync/${source}`,
    data: {
      sync_type: 'manual',
      ...options,
    },
  });
}

export async function listSyncLogs(
  source?: KuaimaiSource,
  limit = 20,
): Promise<SyncLog[]> {
  return request({
    method: 'GET',
    url: '/admin/kuaimai/sync-logs',
    params: { source, limit },
  });
}

// ── 运营管理 ──────────────────────────────────────────────────

export async function listOperators(onlyUnbound = false): Promise<Operator[]> {
  return request({
    method: 'GET',
    url: '/admin/kuaimai/operators',
    params: { only_unbound: onlyUnbound },
  });
}

export async function bindOperator(
  operatorId: string,
  wecomUserid: string,
  operatorUserId?: string,
): Promise<{ bound: boolean }> {
  return request({
    method: 'PATCH',
    url: `/admin/kuaimai/operators/${operatorId}/bind`,
    data: { wecom_userid: wecomUserid, operator_user_id: operatorUserId },
  });
}

export async function unbindOperator(operatorId: string): Promise<{ unbound: boolean }> {
  return request({
    method: 'PATCH',
    url: `/admin/kuaimai/operators/${operatorId}/unbind`,
  });
}
