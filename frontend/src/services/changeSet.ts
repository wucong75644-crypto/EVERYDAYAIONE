/** ChangeSet 状态/审计客户端；不提供直接写业务对象的通用方法。 */
import api from './api';
import type { ChangeSet, ChangeSetTimeline } from '../types/changeset';

interface ApiResponse<T> {
  success: boolean;
  data: T;
}

const BASE = '/change-sets';

export const changeSetService = {
  async get(id: string): Promise<ChangeSet> {
    const res = await api.get<ApiResponse<ChangeSet>>(`${BASE}/${id}`);
    return res.data.data;
  },

  async timeline(id: string): Promise<ChangeSetTimeline> {
    const res = await api.get<ApiResponse<ChangeSetTimeline>>(`${BASE}/${id}/timeline`);
    return res.data.data;
  },

  async cancel(id: string, reason = ''): Promise<ChangeSet> {
    const res = await api.post<ApiResponse<ChangeSet>>(`${BASE}/${id}/cancel`, { reason });
    return res.data.data;
  },

  async confirm(id: string): Promise<ChangeSet> {
    const res = await api.post<ApiResponse<ChangeSet>>(`${BASE}/${id}/confirm`);
    return res.data.data;
  },

  async recover(id: string, idempotencyKey?: string): Promise<ChangeSet> {
    const res = await api.post<ApiResponse<ChangeSet>>(`${BASE}/${id}/recover`, {
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
    });
    return res.data.data;
  },
};
