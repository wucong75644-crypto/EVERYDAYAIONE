/**
 * 任务通知工具函数
 *
 * 统一处理任务完成后的通知逻辑：
 * 1. 标记对话未读
 * 2. 添加通知到列表
 * 3. 添加到最近完成集合
 */

import type { StoreTaskType, CompletedNotification } from '../types/task';

const MAX_NOTIFICATIONS = 50;

/** 通知参数 */
export interface NotifyTaskCompleteParams {
  /** 通知唯一ID（聊天任务用conversationId，媒体任务用taskId） */
  id: string;
  /** 对话ID */
  conversationId: string;
  /** 对话标题 */
  conversationTitle: string;
  /** 任务类型 */
  type: StoreTaskType;
}

/** 通知结果（用于更新 state） */
export interface NotifyTaskCompleteResult {
  /** 新的通知列表 */
  pendingNotifications: CompletedNotification[];
  /** 新的最近完成集合 */
  recentlyCompleted: Set<string>;
}

/**
 * 处理任务完成通知（纯函数，仅计算新状态）
 *
 * 注意：此函数不包含副作用，markConversationUnread 需要在调用方 set 之前单独调用
 *
 * @param params 通知参数
 * @param currentNotifications 当前通知列表
 * @param currentRecentlyCompleted 当前最近完成集合
 * @returns 更新后的状态
 */
export function notifyTaskComplete(
  params: NotifyTaskCompleteParams,
  currentNotifications: CompletedNotification[],
  currentRecentlyCompleted: Set<string>
): NotifyTaskCompleteResult {
  const { id, conversationId, conversationTitle, type } = params;

  // 创建新通知
  const newNotification: CompletedNotification = {
    id,
    conversationId,
    conversationTitle,
    type,
    completedAt: Date.now(),
    isRead: false,
  };

  // 3. 添加通知并限制数量
  let pendingNotifications = [...currentNotifications, newNotification];
  if (pendingNotifications.length > MAX_NOTIFICATIONS) {
    pendingNotifications = pendingNotifications.slice(-MAX_NOTIFICATIONS);
  }

  // 4. 添加到最近完成集合
  const recentlyCompleted = new Set(currentRecentlyCompleted);
  recentlyCompleted.add(conversationId);

  return { pendingNotifications, recentlyCompleted };
}
