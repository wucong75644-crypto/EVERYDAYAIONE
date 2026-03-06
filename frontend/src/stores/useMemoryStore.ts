/**
 * 记忆状态管理
 *
 * 独立 Store，管理记忆列表、设置和 UI 状态。
 */

import { AxiosError } from 'axios';
import { create } from 'zustand';
import type { MemoryItem, MemorySettings } from '../services/memory';
import {
  getMemories,
  addMemory as apiAddMemory,
  updateMemory as apiUpdateMemory,
  deleteMemory as apiDeleteMemory,
  deleteAllMemories as apiDeleteAllMemories,
  getMemorySettings,
  updateMemorySettings as apiUpdateMemorySettings,
} from '../services/memory';

/** 从 Axios 错误中提取后端返回的中文消息 */
function extractErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof AxiosError) {
    const msg = err.response?.data?.error?.message;
    if (typeof msg === 'string') return msg;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

// ============================================================
// 类型定义
// ============================================================

interface MemoryState {
  /** 记忆列表 */
  memories: MemoryItem[];
  /** 列表加载中 */
  loading: boolean;
  /** 操作进行中（增删改） */
  operating: boolean;
  /** 错误信息 */
  error: string | null;

  /** 记忆设置 */
  settings: MemorySettings | null;
  /** 设置加载中 */
  settingsLoading: boolean;

  /** 弹窗是否打开 */
  isModalOpen: boolean;

  /** 搜索关键词 */
  searchQuery: string;

  // 弹窗操作
  openModal: () => void;
  closeModal: () => void;

  // 搜索
  setSearchQuery: (query: string) => void;

  // 数据操作
  fetchMemories: () => Promise<void>;
  addMemory: (content: string) => Promise<boolean>;
  updateMemory: (id: string, content: string) => Promise<boolean>;
  deleteMemory: (id: string) => Promise<boolean>;
  deleteAllMemories: () => Promise<boolean>;

  // 设置操作
  fetchSettings: () => Promise<void>;
  toggleMemoryEnabled: () => Promise<boolean>;

  // WebSocket 记忆提取通知
  onMemoryExtracted: (memories: Array<{ id: string; memory: string }>) => void;

  // 清理
  reset: () => void;
}

// ============================================================
// Store
// ============================================================

export const useMemoryStore = create<MemoryState>((set, get) => ({
  memories: [],
  loading: false,
  operating: false,
  error: null,
  settings: null,
  settingsLoading: false,
  isModalOpen: false,
  searchQuery: '',

  // ========================================
  // 弹窗操作
  // ========================================

  openModal: () => {
    set({ isModalOpen: true });
    // 打开时自动加载数据
    const state = get();
    if (state.memories.length === 0) {
      state.fetchMemories();
    }
    if (!state.settings) {
      state.fetchSettings();
    }
  },

  closeModal: () => set({ isModalOpen: false, searchQuery: '' }),

  // ========================================
  // 搜索
  // ========================================

  setSearchQuery: (query) => set({ searchQuery: query }),

  // ========================================
  // 数据操作
  // ========================================

  fetchMemories: async () => {
    set({ loading: true, error: null });
    try {
      const res = await getMemories();
      set({ memories: res.memories, loading: false });
    } catch (err) {
      set({ error: extractErrorMessage(err, '获取记忆失败'), loading: false });
    }
  },

  addMemory: async (content) => {
    set({ operating: true, error: null });
    try {
      const res = await apiAddMemory(content);
      if (res.count === 0) {
        set({ error: '未提取到新信息，请尝试更明确的描述', operating: false });
        return false;
      }
      const newItems: MemoryItem[] = res.memories.map((m) => ({
        ...m,
        updated_at: m.updated_at ?? null,
      }));
      set((state) => ({
        memories: [...newItems, ...state.memories],
        operating: false,
      }));
      return true;
    } catch (err) {
      set({ error: extractErrorMessage(err, '添加记忆失败'), operating: false });
      return false;
    }
  },

  updateMemory: async (id, content) => {
    set({ operating: true, error: null });
    try {
      const res = await apiUpdateMemory(id, content);
      set((state) => ({
        memories: state.memories.map((m) =>
          m.id === id
            ? { ...m, memory: res.memory, updated_at: res.updated_at }
            : m
        ),
        operating: false,
      }));
      return true;
    } catch (err) {
      set({ error: extractErrorMessage(err, '更新记忆失败'), operating: false });
      return false;
    }
  },

  deleteMemory: async (id) => {
    set({ operating: true, error: null });
    try {
      await apiDeleteMemory(id);
      set((state) => ({
        memories: state.memories.filter((m) => m.id !== id),
        operating: false,
      }));
      return true;
    } catch (err) {
      set({ error: extractErrorMessage(err, '删除记忆失败'), operating: false });
      return false;
    }
  },

  deleteAllMemories: async () => {
    set({ operating: true, error: null });
    try {
      await apiDeleteAllMemories();
      set({ memories: [], operating: false });
      return true;
    } catch (err) {
      set({ error: extractErrorMessage(err, '清空记忆失败'), operating: false });
      return false;
    }
  },

  // ========================================
  // 设置操作
  // ========================================

  fetchSettings: async () => {
    set({ settingsLoading: true });
    try {
      const settings = await getMemorySettings();
      set({ settings, settingsLoading: false });
    } catch {
      set({ settingsLoading: false });
    }
  },

  toggleMemoryEnabled: async () => {
    const current = get().settings;
    if (!current) return false;

    const newEnabled = !current.memory_enabled;
    // 乐观更新
    set({ settings: { ...current, memory_enabled: newEnabled } });

    try {
      const updated = await apiUpdateMemorySettings({
        memory_enabled: newEnabled,
      });
      set({ settings: updated });
      return true;
    } catch {
      // 回滚
      set({ settings: current });
      return false;
    }
  },

  // ========================================
  // WebSocket 通知
  // ========================================

  onMemoryExtracted: (newMemories) => {
    set((state) => {
      const existingIds = new Set(state.memories.map((m) => m.id));
      const toAdd: MemoryItem[] = [];

      // 先更新已存在的记忆（不可变更新）
      let updatedMemories = state.memories;
      for (const item of newMemories) {
        if (existingIds.has(item.id)) {
          updatedMemories = updatedMemories.map((m) =>
            m.id === item.id ? { ...m, memory: item.memory } : m
          );
        } else {
          toAdd.push({
            id: item.id,
            memory: item.memory,
            metadata: { source: 'auto', conversation_id: null },
            created_at: new Date().toISOString(),
            updated_at: null,
          });
        }
      }

      return {
        memories: [...toAdd, ...updatedMemories],
      };
    });
  },

  // ========================================
  // 清理
  // ========================================

  reset: () =>
    set({
      memories: [],
      loading: false,
      operating: false,
      error: null,
      settings: null,
      settingsLoading: false,
      isModalOpen: false,
      searchQuery: '',
    }),
}));
