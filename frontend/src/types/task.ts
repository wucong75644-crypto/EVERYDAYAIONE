/**
 * 任务相关类型定义
 */

/** 任务状态 */
export type TaskStatus = 'pending' | 'processing' | 'success' | 'failed' | 'timeout';

/** 任务类型 */
export type TaskType = 'image' | 'video';
