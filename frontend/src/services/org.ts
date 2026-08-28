/**
 * 企业管理 API
 */

import { request } from './api';

// ── 类型定义 ──

export interface OrgDetail {
  id: string;
  name: string;
  status: string;
  owner_id: string;
  created_at: string;
  member_count?: number;
}

export interface OrgMember {
  user_id: string;
  nickname: string;
  role: string;
  status: string;
  joined_at: string;
}

export interface SearchUserResult {
  found: boolean;
  user: {
    id: string;
    nickname: string;
    phone: string | null;
    status: string;
  } | null;
}

// ── 超管 API ──

export async function listAllOrgs(): Promise<OrgDetail[]> {
  return request({ method: 'GET', url: '/org/admin/all' });
}

export async function searchUser(phone: string): Promise<SearchUserResult> {
  return request({ method: 'GET', url: '/org/admin/search-user', params: { phone } });
}

export async function createOrg(name: string, ownerPhone: string): Promise<{ success: boolean; data: OrgDetail }> {
  return request({ method: 'POST', url: '/org', data: { name, owner_phone: ownerPhone } });
}

export async function suspendOrg(orgId: string): Promise<{ success: boolean; data: OrgDetail }> {
  return request({ method: 'POST', url: `/org/admin/${orgId}/suspend` });
}

export async function restoreOrg(orgId: string): Promise<{ success: boolean; data: OrgDetail }> {
  return request({ method: 'POST', url: `/org/admin/${orgId}/restore` });
}

// ── 企业管理 API ──

export async function getOrgDetail(orgId: string): Promise<OrgDetail> {
  return request({ method: 'GET', url: `/org/${orgId}` });
}

export async function listMembers(orgId: string): Promise<OrgMember[]> {
  return request({ method: 'GET', url: `/org/${orgId}/members` });
}

interface ConfigurationStatus {
  config_key?: string;
  key?: string;
  configured?: boolean;
}

const FORMAL_TO_LEGACY_KEYS: Record<string, string[]> = {
  'ai.dashscope.api_key': ['ai_dashscope_api_key'],
  'ai.openrouter.api_key': ['ai_openrouter_api_key'],
  'ai.kie.api_key': ['ai_kie_api_key'],
  'ai.google.api_key': ['ai_google_api_key'],
  'erp.app_credentials': ['kuaimai_app_key', 'kuaimai_app_secret'],
  'erp.token_pair': ['kuaimai_access_token', 'kuaimai_refresh_token'],
  'wecom.corp_id': ['wecom_corp_id'],
  'wecom.bot_credentials': ['wecom_bot_id', 'wecom_bot_secret'],
  'wecom.oauth_agent_id': ['wecom_agent_id'],
  'wecom.oauth_agent_secret': ['wecom_agent_secret'],
};

export async function listOrgConfigs(orgId: string): Promise<{ success: boolean; data: string[] }> {
  const result = await request<{
    success: boolean;
    data: string[] | ConfigurationStatus[];
  }>({ method: 'GET', url: `/org/${orgId}/configs` });
  if (!result.data.length || typeof result.data[0] === 'string') {
    return result as { success: boolean; data: string[] };
  }
  const keys = (result.data as ConfigurationStatus[]).flatMap((item) => {
    if (!item.configured) return [];
    const formalKey = item.config_key || item.key || '';
    return FORMAL_TO_LEGACY_KEYS[formalKey] || [formalKey];
  });
  return { success: result.success, data: keys };
}

export async function testErpConnection(
  orgId: string,
): Promise<{ success: boolean; message: string }> {
  return request({ method: 'POST', url: `/org/${orgId}/configs/test-erp` });
}

export async function testWecomConnection(
  orgId: string,
): Promise<{ success: boolean; message: string }> {
  return request({ method: 'POST', url: `/org/${orgId}/configs/test-wecom` });
}

export interface WecomFieldStatus {
  configured: boolean;
  source: 'org' | 'system' | null;
}

export async function getWecomStatus(
  orgId: string,
): Promise<{ success: boolean; data: Record<string, WecomFieldStatus> }> {
  return request({ method: 'GET', url: `/org/${orgId}/configs/wecom-status` });
}

export async function updateOrg(
  orgId: string, data: Record<string, string | null>,
): Promise<{ success: boolean }> {
  return request({ method: 'PATCH', url: `/org/${orgId}`, data });
}

export async function setOrgConfig(
  orgId: string, key: string, value: string,
): Promise<{ success: boolean; message: string }> {
  return request({ method: 'PUT', url: `/org/${orgId}/configs`, data: { key, value } });
}

export async function addMember(
  orgId: string, userId: string, role: string = 'member',
): Promise<{ success: boolean }> {
  return request({ method: 'POST', url: `/org/${orgId}/members`, data: { user_id: userId, role } });
}

export async function removeMember(
  orgId: string, userId: string,
): Promise<{ success: boolean }> {
  return request({ method: 'DELETE', url: `/org/${orgId}/members/${userId}` });
}

export async function createInvitation(
  orgId: string, phone: string, role: string = 'member',
): Promise<{ success: boolean; data: { invite_token: string } }> {
  return request({ method: 'POST', url: `/org/${orgId}/invitations`, data: { phone, role } });
}

export interface PendingInvitation {
  invite_token: string;
  org_name: string;
  role: string;
  expires_at: string;
}

export async function listPendingInvitations(): Promise<PendingInvitation[]> {
  return request({ method: 'GET', url: '/org/invitations/pending' });
}

export async function acceptInvitation(
  inviteToken: string,
): Promise<{ success: boolean }> {
  return request({ method: 'POST', url: '/org/invitations/accept', data: { invite_token: inviteToken } });
}
