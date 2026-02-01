/**
 * 任务相关类型定义
 */

// ============================================================
// API 任务状态（后端返回）
// ============================================================

/** API 任务状态 */
export type TaskStatus = 'pending' | 'processing' | 'success' | 'failed' | 'timeout';

/** API 任务类型 */
export type TaskType = 'image' | 'video';

// ============================================================
// Store 任务状态（前端状态管理）
// ============================================================

/** Store 任务状态 */
export type StoreTaskStatus = 'pending' | 'streaming' | 'polling' | 'completed' | 'error';

/** Store 任务类型（包含聊天） */
export type StoreTaskType = 'chat' | 'image' | 'video';

/** 完成通知 */
export interface CompletedNotification {
  id: string;
  conversationId: string;
  conversationTitle: string;
  type: StoreTaskType;
  completedAt: number;
  isRead: boolean;
}
