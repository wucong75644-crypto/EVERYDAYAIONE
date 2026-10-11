import { request } from './api';

export type ReachPlatform = 'twitter' | 'bilibili' | 'reddit' | 'xiaohongshu' | 'youtube';
export interface ReachConnection {
  id: string;
  platform: ReachPlatform;
  account_id: string;
  display_name: string;
  status: string;
  login_state?: 'unchecked' | 'connected' | 'expired' | 'error';
  checked_at?: string;
}
export const listReachConnections = () => request<ReachConnection[]>({ method: 'GET', url: '/agent-reach/connections' });
export const createReachConnection = (data: { platform: ReachPlatform; account_id: string; display_name: string; secret_json: string }) =>
  request<ReachConnection>({ method: 'POST', url: '/agent-reach/connections', data });
export const grantReachConnection = (id: string, user_id: string, can_read: boolean, can_write: boolean) =>
  request({ method: 'PUT', url: `/agent-reach/connections/${id}/grants`, data: { user_id, can_read, can_write } });
export const revokeReachConnection = (id: string) => request({ method: 'DELETE', url: `/agent-reach/connections/${id}` });

export interface ReachOperation {
  id: string; platform: ReachPlatform; account_id: string; state: string;
  receipt: { action?: string; remote_id?: string; stage?: string }; created_at: string;
}
export const listReachOperations = () => request<ReachOperation[]>({ method: 'GET', url: '/agent-reach/operations' });

export interface ReachLoginMethod {
  platform: ReachPlatform; method: 'qr' | 'oauth' | 'import'; enabled: boolean; message: string;
}
export interface ReachLoginSession {
  id: string; platform: ReachPlatform;
  state: 'waiting' | 'confirming' | 'connected' | 'expired' | 'failed' | 'cancelled';
  expires_at: number; image?: string; authorize_url?: string; connection?: ReachConnection; message?: string;
}
export const listReachLoginMethods = () => request<ReachLoginMethod[]>({ method: 'GET', url: '/agent-reach/login-methods' });
export const startReachLogin = (platform: ReachPlatform, connection_id?: string) => request<ReachLoginSession>({ method: 'POST', url: '/agent-reach/login-sessions', data: { platform, connection_id } });
export const pollReachLogin = (id: string) => request<ReachLoginSession>({ method: 'GET', url: `/agent-reach/login-sessions/${id}` });
export const cancelReachLogin = (id: string) => request({ method: 'DELETE', url: `/agent-reach/login-sessions/${id}` });
export const importReachSession = (platform: ReachPlatform, cookie_header: string, connection_id?: string) => request<ReachConnection>({ method: 'POST', url: '/agent-reach/session-import', data: { platform, cookie_header, connection_id } });
export const checkReachConnection = (id: string) => request<{ state: string; message: string }>({ method: 'POST', url: `/agent-reach/connections/${id}/check` });
