/**
 * 订阅状态管理
 *
 * 管理用户的模型订阅列表，与首页模型广场和聊天页模型选择器联动。
 */

import { create } from 'zustand';
import type { ModelInfo } from '../types/subscription';
import {
  getModels,
  getSubscriptions,
  subscribeModel,
  unsubscribeModel,
} from '../services/subscription';
import { logger } from '../utils/logger';

interface SubscriptionState {
  /** 已订阅的模型 ID 列表 */
  subscribedModelIds: string[];
  /** 所有模型的后端信息（status） */
  modelInfoMap: Record<string, ModelInfo>;
  /** 是否正在加载订阅列表 */
  isLoading: boolean;
  /** 正在执行订阅/取消操作的模型 ID */
  subscribingIds: string[];

  /** 加载模型列表（公开，无需登录） */
  fetchModels: () => Promise<void>;
  /** 加载用户订阅列表（需登录） */
  fetchSubscriptions: () => Promise<void>;
  /** 订阅模型 */
  subscribe: (modelId: string) => Promise<boolean>;
  /** 取消订阅 */
  unsubscribe: (modelId: string) => Promise<boolean>;
  /** 判断模型是否已订阅 */
  isSubscribed: (modelId: string) => boolean;
  /** 判断模型是否正在操作中 */
  isSubscribing: (modelId: string) => boolean;
  /** 清除订阅数据（退出登录时调用） */
  clearSubscriptions: () => void;
}

export const useSubscriptionStore = create<SubscriptionState>((set, get) => ({
  subscribedModelIds: [],
  modelInfoMap: {},
  isLoading: false,
  subscribingIds: [],

  fetchModels: async () => {
    try {
      const res = await getModels();
      const map: Record<string, ModelInfo> = {};
      for (const m of res.models) {
        map[m.id] = m;
      }
      set({ modelInfoMap: map });
    } catch (error) {
      logger.error('subscription', '获取模型列表失败', error);
    }
  },

  fetchSubscriptions: async () => {
    set({ isLoading: true });
    try {
      const res = await getSubscriptions();
      set({
        subscribedModelIds: res.subscriptions.map((s) => s.model_id),
        isLoading: false,
      });
    } catch (error) {
      logger.error('subscription', '获取订阅列表失败', error);
      set({ isLoading: false });
    }
  },

  subscribe: async (modelId: string) => {
    const { subscribingIds } = get();
    if (subscribingIds.includes(modelId)) return false;

    set({ subscribingIds: [...subscribingIds, modelId] });
    try {
      await subscribeModel(modelId);
      const { subscribedModelIds } = get();
      if (!subscribedModelIds.includes(modelId)) {
        set({ subscribedModelIds: [...subscribedModelIds, modelId] });
      }
      return true;
    } catch (error) {
      logger.error('subscription', '订阅失败', error, { modelId });
      throw error;
    } finally {
      set({
        subscribingIds: get().subscribingIds.filter((id) => id !== modelId),
      });
    }
  },

  unsubscribe: async (modelId: string) => {
    const { subscribingIds } = get();
    if (subscribingIds.includes(modelId)) return false;

    set({ subscribingIds: [...subscribingIds, modelId] });
    try {
      await unsubscribeModel(modelId);
      set({
        subscribedModelIds: get().subscribedModelIds.filter((id) => id !== modelId),
      });
      return true;
    } catch (error) {
      logger.error('subscription', '取消订阅失败', error, { modelId });
      throw error;
    } finally {
      set({
        subscribingIds: get().subscribingIds.filter((id) => id !== modelId),
      });
    }
  },

  isSubscribed: (modelId: string) => {
    return get().subscribedModelIds.includes(modelId);
  },

  isSubscribing: (modelId: string) => {
    return get().subscribingIds.includes(modelId);
  },

  clearSubscriptions: () => {
    set({ subscribedModelIds: [], subscribingIds: [] });
  },
}));
