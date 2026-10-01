import toast from 'react-hot-toast';
import { updateConversation, type ChatSettings } from '../services/conversation';
import { logger } from './logger';

interface PendingSettings {
  revision: string;
  settings: ChatSettings;
}

const pendingMemory = new Map<string, PendingSettings>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const writes = new Map<string, Promise<void>>();

function storageKey(userId: string, conversationId: string): string {
  return `pending_conversation_settings:${userId}:${conversationId}`;
}

export function getPendingConversationSettings(userId: string, conversationId: string): PendingSettings | null {
  const key = storageKey(userId, conversationId);
  try {
    const value = localStorage.getItem(key);
    if (value) {
      const pending = JSON.parse(value) as PendingSettings;
      if (typeof pending.revision === 'string' && pending.settings && typeof pending.settings === 'object') {
        return pending;
      }
    }
  } catch (error) {
    logger.error('settings', '读取待保存对话设置失败', error);
  }
  return pendingMemory.get(key) ?? null;
}

export function retryPendingConversationSettings(userId: string, conversationId: string): void {
  const key = storageKey(userId, conversationId);
  clearTimeout(timers.get(key));
  timers.delete(key);
  // Serialize writes so an older request cannot overwrite the newest selection.
  const previous = writes.get(key) ?? Promise.resolve();
  const next = previous.then(async () => {
    const pending = getPendingConversationSettings(userId, conversationId);
    if (!pending) return;
    try {
      await updateConversation(conversationId, { chat_settings: pending.settings });
      if (getPendingConversationSettings(userId, conversationId)?.revision === pending.revision) {
        pendingMemory.delete(key);
        localStorage.removeItem(key);
      }
    } catch (error) {
      logger.error('settings', '保存对话设置失败，保留本地选择', error);
      toast.error('设置保存失败，已保留本地选择，请检查网络后重试');
    }
  });
  writes.set(key, next);
  void next.finally(() => {
    if (writes.get(key) === next) writes.delete(key);
  });
}

export function clearNewConversationSettings(userId: string): void {
  const key = storageKey(userId, 'new');
  pendingMemory.delete(key);
  try {
    localStorage.removeItem(key);
  } catch (error) {
    logger.error('settings', '清除新对话草稿设置失败', error);
  }
}

export function saveConversationSettings(userId: string, conversationId: string | null, settings: ChatSettings): void {
  const key = storageKey(userId, conversationId ?? 'new');
  const pending = { revision: crypto.randomUUID(), settings };
  pendingMemory.set(key, pending);
  try {
    // Synchronous journal closes the debounce/refresh window.
    localStorage.setItem(key, JSON.stringify(pending));
  } catch (error) {
    logger.error('settings', '暂存对话设置失败', error);
    toast.error('浏览器无法暂存设置，保存完成前请勿刷新');
  }
  if (!conversationId) return; // The first send persists this snapshot when creating the conversation.
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => retryPendingConversationSettings(userId, conversationId), 500));
}
