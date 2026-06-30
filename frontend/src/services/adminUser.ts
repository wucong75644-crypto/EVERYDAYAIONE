/**
 * 管理员用户管理 API（仅 super_admin 可调用）
 *
 * 对应后端 /api/admin/users 系列接口（admin_users.py / admin_users_zip.py）
 */

import { request, API_BASE_URL } from './api';

// ── 类型定义 ─────────────────────────────────────────────

export interface AdminUserListItem {
  id: string;
  nickname: string;
  phone: string | null;
  avatar_url: string | null;
  role: 'user' | 'admin' | 'super_admin';
  credits: number;
  status: 'active' | 'disabled';
  current_org_id: string | null;
  org_name: string | null;
  created_at: string;
  last_login_at: string | null;
  last_active_at: string | null;
}

export interface AdminUserSummary extends AdminUserListItem {
  org_name: string | null;
  total_consumed: number;
  conversation_count: number;
}

export interface AdminUserListResponse {
  items: AdminUserListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreditsHistoryItem {
  id: string;
  user_id: string;
  change_amount: number;
  balance_after: number;
  change_type: string;
  description: string | null;
  operator_id: string | null;
  operator_name: string | null;
  related_id: string | null;
  created_at: string;
}

export interface CreditsHistoryResponse {
  items: CreditsHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface RechargeResponse {
  success: boolean;
  new_balance: number;
  delta: number;
}

export interface ConversationListItem {
  id: string;
  title: string;
  model_id: string | null;
  message_count: number;
  credits_consumed: number;
  last_message_preview: string | null;
  updated_at: string;
  created_at: string;
}

export interface ConversationListResponse {
  items: ConversationListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AttachmentPart {
  url: string;
  original_url?: string;
  thumbnail_url?: string | null;
  preview_url?: string;
  download_url?: string;
  name: string;
  type: 'file' | 'image';
  size: number | null;
  mime: string | null;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  content_parsed: unknown;
  image_url: string | null;
  video_url: string | null;
  credits_cost: number;
  is_error: boolean;
  generation_params: Record<string, unknown> | null;
  created_at: string;
  attachments: AttachmentPart[];
}

export interface ConversationMessagesResponse {
  conversation: { id: string; title: string };
  items: ConversationMessage[];
  total: number;
}

export interface UploadAsset {
  url: string;
  original_url?: string;
  thumbnail_url?: string | null;
  preview_url?: string;
  download_url?: string;
  name: string;
  type: 'file' | 'image';
  size: number | null;
  mime: string | null;
  message_id: string;
  conversation_id: string;
  created_at: string;
}

export interface UploadAssetsResponse {
  items: UploadAsset[];
  total: number;
  page: number;
  page_size: number;
}

export interface GenerationAsset {
  kind: 'image' | 'video';
  id: string;
  url: string;
  original_url?: string;
  thumbnail_url?: string | null;
  preview_url?: string;
  download_url?: string;
  prompt: string | null;
  negative_prompt: string | null;
  model_id: string | null;
  size: string | null;
  credits_cost: number;
  conversation_id: string | null;
  created_at: string;
}

export interface GenerationAssetsResponse {
  items: GenerationAsset[];
  total: number;
  page: number;
  page_size: number;
}

// ── 用户列表 / 概览 ────────────────────────────────────

export function listAdminUsers(params: {
  search?: string;
  org_id?: string;
  page?: number;
  page_size?: number;
}): Promise<AdminUserListResponse> {
  return request({ method: 'GET', url: '/admin/users', params });
}

export function getAdminUserSummary(uid: string): Promise<AdminUserSummary> {
  return request({ method: 'GET', url: `/admin/users/${uid}/summary` });
}

// ── 积分 ───────────────────────────────────────────────

export function rechargeUserCredits(
  uid: string,
  body: { delta: number; reason?: string; org_id?: string | null },
): Promise<RechargeResponse> {
  return request({ method: 'POST', url: `/admin/users/${uid}/credits/recharge`, data: body });
}

export function getUserCreditsHistory(
  uid: string,
  params: { page?: number; page_size?: number } = {},
): Promise<CreditsHistoryResponse> {
  return request({ method: 'GET', url: `/admin/users/${uid}/credits/history`, params });
}

// ── 对话 / 消息 ────────────────────────────────────────

export function listUserConversations(
  uid: string,
  params: { page?: number; page_size?: number } = {},
): Promise<ConversationListResponse> {
  return request({ method: 'GET', url: `/admin/users/${uid}/conversations`, params });
}

export function getUserConversationMessages(
  uid: string,
  cid: string,
  params: { limit?: number } = {},
): Promise<ConversationMessagesResponse> {
  return request({
    method: 'GET',
    url: `/admin/users/${uid}/conversations/${cid}/messages`,
    params,
  });
}

// ── 资产 ───────────────────────────────────────────────

export function listUserUploads(
  uid: string,
  params: { page?: number; page_size?: number; days?: number } = {},
): Promise<UploadAssetsResponse> {
  return request({ method: 'GET', url: `/admin/users/${uid}/uploads`, params });
}

export function listUserGenerations(
  uid: string,
  params: { page?: number; page_size?: number; kind?: 'image' | 'video' } = {},
): Promise<GenerationAssetsResponse> {
  return request({ method: 'GET', url: `/admin/users/${uid}/generations`, params });
}

// ── 批量 ZIP 下载 ──────────────────────────────────────

/**
 * 触发批量 ZIP 下载（接受 OSS CDN URL 数组）。
 *
 * 内部用 fetch 拉取流式 ZIP → Blob → a.download，避免 axios 把流读到内存的额外成本。
 * 与 services/workspace.ts:downloadWorkspaceZip 同样的下载范式。
 */
export async function downloadUserAssetsZip(
  uid: string,
  body: { urls: string[]; filenames?: string[]; zip_name?: string },
): Promise<void> {
  const token = localStorage.getItem('access_token');
  const orgId = localStorage.getItem('current_org_id');

  const resp = await fetch(`${API_BASE_URL}/admin/users/${uid}/download_zip`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { 'X-Org-Id': orgId } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    let detail = '下载失败';
    try {
      const err = await resp.json();
      detail = err.detail || err.message || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  // 解析文件名（filename*=UTF-8'' 优先）
  const disposition = resp.headers.get('Content-Disposition') || '';
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const asciiMatch = disposition.match(/filename="([^"]+)"/i);
  let filename = body.zip_name || 'download.zip';
  if (utf8Match) {
    try {
      filename = decodeURIComponent(utf8Match[1]);
    } catch {
      // ignore
    }
  } else if (asciiMatch) {
    filename = asciiMatch[1];
  }

  const blob = await resp.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(objectUrl);
}
