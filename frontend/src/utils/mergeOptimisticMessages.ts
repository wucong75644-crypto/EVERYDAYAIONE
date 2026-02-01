/**
 * 合并持久化消息和乐观更新消息的工具函数
 *
 * 处理以下场景：
 * 1. 去重：已持久化的消息不重复显示
 * 2. temp- 用户消息：优先 client_request_id 精确匹配，Fallback 到内容+时间匹配
 * 3. streaming- 消息：区分聊天流式、媒体任务占位符、已完成流式（精确内容匹配）
 * 4. 按时间排序
 *
 * 任务0.2修复（2026-02-01）：
 * - 增强 temp- 消息去重：优先使用 client_request_id 避免误判
 * - 修复 streaming- 消息去重：移除时间阈值，使用精确内容匹配
 * - 解决"AI回复 → 用户消息 → AI回复"重复显示问题
 */

import type { Message } from '../services/message';
import { isMediaPlaceholder } from '../constants/placeholder';

/** 判断 temp 消息是否已被替换的时间阈值（ms）*/
const TEMP_MESSAGE_MATCH_THRESHOLD_MS = 30000; // 30秒（兼容弱网环境）

export interface RuntimeState {
  streamingMessageId: string | null;
  optimisticMessages: Message[];
}

/**
 * 检查 temp 消息是否已被持久化消息替换
 * 优先使用 client_request_id 精确匹配，Fallback 到内容+时间匹配
 */
function isTempMessageReplaced(
  tempMessage: Message,
  persistedMessages: Message[]
): boolean {
  // ✅ 任务0.2：优先使用 client_request_id 精确匹配（避免内容+时间误判）
  if (tempMessage.client_request_id) {
    const matchByClientId = persistedMessages.some(
      (pm) => pm.client_request_id === tempMessage.client_request_id
    );
    if (matchByClientId) return true;
  }

  // Fallback：内容+时间匹配（兼容旧消息或无 client_request_id 的场景）
  const tempTime = new Date(tempMessage.created_at).getTime();
  return persistedMessages.some((pm) => {
    if (pm.role !== 'user' || pm.content !== tempMessage.content) {
      return false;
    }
    const persistedTime = new Date(pm.created_at).getTime();
    const timeDiff = Math.abs(persistedTime - tempTime);
    return timeDiff < TEMP_MESSAGE_MATCH_THRESHOLD_MS;
  });
}

/**
 * 检查 streaming 消息是否已被持久化消息替换
 * 匹配条件：角色为 assistant + 内容完全相同（流式完成后内容应完全一致）
 *
 * 注：时间戳排序问题已通过前端传递 created_at 给后端解决，无需时间阈值检查
 */
function isStreamingMessageReplaced(
  streamingMessage: Message,
  persistedMessages: Message[]
): boolean {
  // ✅ 精确内容匹配，无时间阈值（流式完成后内容应完全相同）
  return persistedMessages.some((pm) => {
    return pm.role === 'assistant' && pm.content === streamingMessage.content;
  });
}

/**
 * 合并持久化消息和乐观更新消息
 *
 * @param persistedMessages - 已持久化的消息列表
 * @param runtimeState - 运行时状态（包含乐观更新消息）
 * @returns 合并并排序后的消息列表
 */
export function mergeOptimisticMessages(
  persistedMessages: Message[],
  runtimeState: RuntimeState | undefined
): Message[] {
  // 无运行时状态或无乐观消息，直接返回持久化消息
  if (!runtimeState || runtimeState.optimisticMessages.length === 0) {
    return persistedMessages;
  }

  // 创建持久化消息的ID集合
  const persistedIds = new Set(persistedMessages.map((m) => m.id));

  // 过滤出需要显示的乐观消息
  const newOptimisticMessages = runtimeState.optimisticMessages.filter((m) => {
    // 已存在于持久化消息中（通过ID），跳过
    if (persistedIds.has(m.id)) return false;

    // temp- 用户消息：检查是否已有对应的持久化消息（内容+时间匹配）
    if (m.id.startsWith('temp-') && m.role === 'user') {
      return !isTempMessageReplaced(m, persistedMessages);
    }

    // streaming- AI消息需要区分聊天流式和媒体任务占位符
    if (m.id.startsWith('streaming-')) {
      // 检查是否是当前正在进行的聊天流式消息
      if (m.id === runtimeState.streamingMessageId) {
        // 聊天流式消息：显示（正在生成中）
        return true;
      }

      // 检查是否是媒体任务占位符（使用统一常量判断）
      if (isMediaPlaceholder(m)) {
        // 媒体任务占位符：始终显示（会被 replaceMediaPlaceholder 替换为真实消息）
        return true;
      }

      // 已完成的聊天流式消息：检查是否已被持久化（内容+时间匹配）
      return !isStreamingMessageReplaced(m, persistedMessages);
    }

    // 其他消息：显示
    return true;
  });

  // 合并并按时间排序
  const combined = [...persistedMessages, ...newOptimisticMessages];
  combined.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  return combined;
}
