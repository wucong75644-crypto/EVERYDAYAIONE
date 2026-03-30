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

// ── 企业管理 API ──

export async function getOrgDetail(orgId: string): Promise<OrgDetail> {
  return request({ method: 'GET', url: `/org/${orgId}` });
}

export async function listMembers(orgId: string): Promise<OrgMember[]> {
  return request({ method: 'GET', url: `/org/${orgId}/members` });
}

export async function listOrgConfigs(orgId: string): Promise<{ success: boolean; data: string[] }> {
  return request({ method: 'GET', url: `/org/${orgId}/configs` });
}

export async function testErpConnection(
  orgId: string,
): Promise<{ success: boolean; message: string }> {
  return request({ method: 'POST', url: `/org/${orgId}/configs/test-erp` });
}

export async function updateOrg(
  orgId: string, data: { wecom_corp_id?: string },
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
