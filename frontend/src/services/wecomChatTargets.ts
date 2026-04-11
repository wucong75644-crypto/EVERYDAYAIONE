/**
 * 企微聊天目标管理 Service
 *
 * 后端路由: backend/api/routes/wecom_chat_targets.py
 */
import api from './api';
import type { WecomGroup, UpdateChatNameDto } from '../types/wecomChatTargets';

interface ApiResponse<T> {
  success: boolean;
  data: T;
  total?: number;
}

const BASE = '/wecom-chat-targets';

export const wecomChatTargetsService = {
  /** 列出企业所有群聊 */
  async listGroups(): Promise<WecomGroup[]> {
    const res = await api.get<ApiResponse<WecomGroup[]>>(`${BASE}/groups`);
    return res.data.data;
  },

  /** 修改群名 */
  async updateName(targetId: string, dto: UpdateChatNameDto): Promise<void> {
    await api.patch(`${BASE}/${targetId}/name`, dto);
  },
};
