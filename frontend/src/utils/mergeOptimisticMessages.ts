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
 *
 * 任务7.2优化（2026-02-02）：
 * - 算法复杂度从 O(n²) 优化为 O(n)
 * - 预处理持久化消息为 Map/Set 结构，查找复杂度 O(1)
 */

import type { Message } from '../services/message';
import { isMediaPlaceholder } from '../constants/placeholder';

/** 判断 temp 消息是否已被替换的时间阈值（ms）*/
const TEMP_MESSAGE_MATCH_THRESHOLD_MS = 30000; // 30秒（兼容弱网环境）

export interface RuntimeState {
  streamingMessageId: string | null;
  optimisticMessages: Message[];
}

/** 预处理后的持久化消息索引（用于 O(1) 查找） */
interface PersistedMessagesIndex {
  /** ID 集合 */
  idSet: Set<string>;
  /** client_request_id → Message */
  clientRequestIdMap: Map<string, Message>;
  /** 用户消息内容 → 消息列表（用于时间匹配） */
  userContentMap: Map<string, Message[]>;
  /** AI 消息内容集合 */
  assistantContentSet: Set<string>;
}

/**
 * 预处理持久化消息，创建索引结构
 * 时间复杂度：O(n)
 */
function buildPersistedMessagesIndex(persistedMessages: Message[]): PersistedMessagesIndex {
  const idSet = new Set<string>();
  const clientRequestIdMap = new Map<string, Message>();
  const userContentMap = new Map<string, Message[]>();
  const assistantContentSet = new Set<string>();

  for (const pm of persistedMessages) {
    idSet.add(pm.id);

    if (pm.client_request_id) {
      clientRequestIdMap.set(pm.client_request_id, pm);
    }

    if (pm.role === 'user') {
      const existing = userContentMap.get(pm.content) || [];
      existing.push(pm);
      userContentMap.set(pm.content, existing);
    } else if (pm.role === 'assistant') {
      assistantContentSet.add(pm.content);
    }
  }

  return { idSet, clientRequestIdMap, userContentMap, assistantContentSet };
}

/**
 * 检查 temp 消息是否已被持久化消息替换
 * 优先使用 client_request_id 精确匹配，Fallback 到内容+时间匹配
 * 时间复杂度：O(1) 平均，O(k) 最坏（k 为相同内容的消息数，通常 k << n）
 */
function isTempMessageReplaced(
  tempMessage: Message,
  index: PersistedMessagesIndex
): boolean {
  // ✅ 优先使用 client_request_id 精确匹配 O(1)
  if (tempMessage.client_request_id) {
    if (index.clientRequestIdMap.has(tempMessage.client_request_id)) {
      return true;
    }
  }

  // Fallback：内容+时间匹配
  const sameContentMessages = index.userContentMap.get(tempMessage.content);
  if (!sameContentMessages || sameContentMessages.length === 0) {
    return false;
  }

  const tempTime = new Date(tempMessage.created_at).getTime();
  return sameContentMessages.some((pm) => {
    const persistedTime = new Date(pm.created_at).getTime();
    const timeDiff = Math.abs(persistedTime - tempTime);
    return timeDiff < TEMP_MESSAGE_MATCH_THRESHOLD_MS;
  });
}

/**
 * 检查 streaming 消息是否已被持久化消息替换
 * 匹配条件：角色为 assistant + 内容完全相同
 * 时间复杂度：O(1)
 */
function isStreamingMessageReplaced(
  streamingMessage: Message,
  index: PersistedMessagesIndex
): boolean {
  return index.assistantContentSet.has(streamingMessage.content);
}

/**
 * 合并持久化消息和乐观更新消息
 *
 * @param persistedMessages - 已持久化的消息列表
 * @param runtimeState - 运行时状态（包含乐观更新消息）
 * @returns 合并并排序后的消息列表
 *
 * 时间复杂度：O(n + m)，其中 n 为持久化消息数，m 为乐观消息数
 */
export function mergeOptimisticMessages(
  persistedMessages: Message[],
  runtimeState: RuntimeState | undefined
): Message[] {
  // 无运行时状态或无乐观消息，直接返回持久化消息
  if (!runtimeState || runtimeState.optimisticMessages.length === 0) {
    return persistedMessages;
  }

  // ✅ 任务7.2：预处理为索引结构，O(n) 一次性构建
  const index = buildPersistedMessagesIndex(persistedMessages);

  // 过滤出需要显示的乐观消息，每次查找 O(1)
  const newOptimisticMessages = runtimeState.optimisticMessages.filter((m) => {
    // 已存在于持久化消息中（通过ID），跳过 O(1)
    if (index.idSet.has(m.id)) return false;

    // temp- 用户消息：检查是否已有对应的持久化消息
    if (m.id.startsWith('temp-') && m.role === 'user') {
      return !isTempMessageReplaced(m, index);
    }

    // streaming- AI消息需要区分聊天流式和媒体任务占位符
    if (m.id.startsWith('streaming-')) {
      // 检查是否是当前正在进行的聊天流式消息
      if (m.id === runtimeState.streamingMessageId) {
        return true;
      }

      // 检查是否是媒体任务占位符
      if (isMediaPlaceholder(m)) {
        return true;
      }

      // 已完成的聊天流式消息：检查是否已被持久化 O(1)
      return !isStreamingMessageReplaced(m, index);
    }

    // 其他消息：显示
    return true;
  });

  // 合并并按时间排序
  const combined = [...persistedMessages, ...newOptimisticMessages];
  combined.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  return combined;
}
