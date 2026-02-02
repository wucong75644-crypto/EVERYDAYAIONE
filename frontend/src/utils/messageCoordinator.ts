/**
 * 消息协调层 - 统一管理跨 Store 操作
 *
 * 解决问题：
 * 1. updateMessageId 重复调用（ChatStore + RuntimeStore）
 * 2. TaskStore 直接依赖 ChatStore（markConversationUnread）
 *
 * 设计原则：
 * - 每个 Store 只管理自己的数据
 * - 跨 Store 操作通过协调层统一处理
 * - 相关操作要么都成功要么都失败
 */

import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import type { Message } from '../services/message';

export const messageCoordinator = {
  /**
   * 确认用户消息（统一 updateMessageId）
   *
   * 替代：chatSender.ts 中的两次 updateMessageId 调用
   * - ChatStore.updateMessageId()
   * - RuntimeStore.updateMessageId()
   *
   * @param params.conversationId - 对话 ID
   * @param params.clientRequestId - 客户端请求 ID（temp-xxx）
   * @param params.newId - 后端返回的真实消息 ID
   * @param params.message - 完整的用户消息（用于确保缓存中有消息）
   */
  confirmUserMessage(params: {
    conversationId: string;
    clientRequestId: string;
    newId: string;
    message: Message;
  }): void {
    const { conversationId, clientRequestId, newId, message } = params;

    // 1. 更新 ChatStore 中的消息 ID
    const chatStore = useChatStore.getState();
    chatStore.updateMessageId(conversationId, clientRequestId, newId);

    // 2. 确保消息在缓存中（如果 updateMessageId 失败，直接追加）
    const cached = chatStore.messageCache.get(conversationId);
    const messageExists = cached?.messages.some((m) => m.id === newId);
    if (!messageExists) {
      chatStore.appendMessage(conversationId, message);
    }

    // 3. 更新 RuntimeStore 中的消息 ID
    useConversationRuntimeStore.getState().updateMessageId(
      conversationId,
      clientRequestId,
      newId
    );
  },

  /**
   * 标记对话未读
   *
   * 从 TaskStore 提取，解耦 TaskStore 对 ChatStore 的依赖
   * 调用方：useMessageCallbacks、mediaGenerationCore、taskRestoration
   *
   * @param conversationId - 对话 ID
   */
  markConversationUnread(conversationId: string): void {
    useChatStore.getState().markConversationUnread(conversationId);
  },
};
