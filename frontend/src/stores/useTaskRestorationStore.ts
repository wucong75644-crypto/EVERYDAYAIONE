/**
 * 任务恢复状态协调器
 *
 * 职责：
 * 1. 跟踪 hydrate 完成状态
 * 2. 标记 Phase 1（占位符创建）完成，供 MessageArea 协调骨架屏
 *
 * 设计说明（v5.0 精简版）：
 * - hydrateComplete：zustand persist 恢复完成标记，Phase 1 的前置条件
 * - placeholdersReady：Phase 1 完成标记，MessageArea 骨架屏依赖
 * - 防重复：由 WebSocketContext 中的 restorationResultRef 负责
 * - WS 状态：直接使用 ws.isConnected，不再同步到此 Store
 */

import { create } from 'zustand';
import { logger } from '../utils/logger';

interface TaskRestorationState {
  /** zustand persist hydrate 完成标记 */
  hydrateComplete: boolean;
  /** Phase 1 完成标记：占位符已创建，MessageArea 可以渲染 */
  placeholdersReady: boolean;

  setHydrateComplete: () => void;
  setPlaceholdersReady: () => void;
  reset: () => void;
}

export const useTaskRestorationStore = create<TaskRestorationState>((set) => ({
  hydrateComplete: false,
  placeholdersReady: false,

  setHydrateComplete: () => {
    set({ hydrateComplete: true });
    logger.debug('task:restore', 'Hydrate complete');
  },

  setPlaceholdersReady: () => {
    set({ placeholdersReady: true });
    logger.debug('task:restore', 'Phase 1 complete: placeholders ready');
  },

  reset: () => {
    set({
      hydrateComplete: false,
      placeholdersReady: false,
    });
    logger.debug('task:restore', 'Restoration state reset');
  },
}));
