/**
 * 任务恢复状态协调器
 *
 * 职责：
 * 1. 跟踪 hydrate 和 WebSocket 连接状态
 * 2. 在两者都就绪后触发统一恢复流程
 * 3. 防止重复恢复（strict mode / 多标签页）
 *
 * 设计说明：
 * - 解决 hydrate 与 WebSocket 的竞态条件
 * - 确保恢复时机正确：等待两个条件都满足
 * - 支持重连后重新恢复
 */

import { create } from 'zustand';
import { logger } from '../utils/logger';

interface TaskRestorationState {
  // 状态标记
  hydrateComplete: boolean;
  wsConnected: boolean;
  restorationComplete: boolean;
  restorationInProgress: boolean;

  // 操作
  setHydrateComplete: () => void;
  setWsConnected: (connected: boolean) => void;
  startRestoration: () => boolean; // 返回是否可以开始
  completeRestoration: () => void;
  reset: () => void;

  // 内部：检查是否就绪
  isReady: () => boolean;
}

export const useTaskRestorationStore = create<TaskRestorationState>((set, get) => ({
  hydrateComplete: false,
  wsConnected: false,
  restorationComplete: false,
  restorationInProgress: false,

  setHydrateComplete: () => {
    set({ hydrateComplete: true });
    logger.debug('task:restore', 'Hydrate complete, checking restoration readiness');
  },

  setWsConnected: (connected: boolean) => {
    const prev = get().wsConnected;
    set({ wsConnected: connected });

    if (connected && !prev) {
      logger.debug('task:restore', 'WebSocket connected, checking restoration readiness');
    } else if (!connected && prev) {
      // 断开连接时，重置恢复状态以便重连后能重新恢复
      logger.debug('task:restore', 'WebSocket disconnected, resetting restoration state');
      set({
        restorationComplete: false,
        restorationInProgress: false,
      });
    }
  },

  startRestoration: () => {
    const state = get();

    // 防止重复恢复
    if (state.restorationComplete || state.restorationInProgress) {
      logger.debug('task:restore', 'Restoration already done or in progress, skipping', {
        complete: state.restorationComplete,
        inProgress: state.restorationInProgress,
      });
      return false;
    }

    // 检查前置条件
    if (!state.hydrateComplete || !state.wsConnected) {
      logger.debug('task:restore', 'Not ready for restoration', {
        hydrateComplete: state.hydrateComplete,
        wsConnected: state.wsConnected,
      });
      return false;
    }

    set({ restorationInProgress: true });
    logger.info('task:restore', 'Starting task restoration');
    return true;
  },

  completeRestoration: () => {
    set({
      restorationComplete: true,
      restorationInProgress: false,
    });
    logger.info('task:restore', 'Task restoration completed');
  },

  reset: () => {
    set({
      hydrateComplete: false,
      wsConnected: false,
      restorationComplete: false,
      restorationInProgress: false,
    });
    logger.debug('task:restore', 'Restoration state reset');
  },

  isReady: () => {
    const state = get();
    return (
      state.hydrateComplete &&
      state.wsConnected &&
      !state.restorationComplete &&
      !state.restorationInProgress
    );
  },
}));

/**
 * 辅助 hook：检查是否可以开始恢复
 */
export function useIsRestorationReady() {
  return useTaskRestorationStore((state) =>
    state.hydrateComplete &&
    state.wsConnected &&
    !state.restorationComplete &&
    !state.restorationInProgress
  );
}

/**
 * 辅助函数：用于重连后重置恢复状态
 * 由于 hydrate 只执行一次，重连时需要手动标记 hydrate 完成
 */
export function resetForReconnect() {
  const store = useTaskRestorationStore.getState();
  store.reset();
  // hydrate 已完成（不会重新执行），直接标记
  store.setHydrateComplete();
}
