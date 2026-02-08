/**
 * 消息协调层 - 统一管理跨 Store 操作
 *
 * 解决问题：
 * 1. updateMessageId 重复调用
 * 2. 解耦不同模块对 Store 的直接依赖
 *
 * 设计原则：
 * - 使用统一的 useMessageStore 管理所有状态
 * - 跨模块操作通过协调层统一处理
 */

import { useMessageStore, type Message } from '../stores/useMessageStore';

export const messageCoordinator = {
  /**
   * 确认用户消息（统一 updateMessageId）
   *
   * 由 sendMessage 调用，统一更新消息 ID
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

    const store = useMessageStore.getState();

    // 1. 更新消息 ID（从 temp-xxx 到真实 ID）
    store.updateMessage(`temp-${clientRequestId}`, { id: newId });

    // 2. 更新乐观消息 ID
    store.updateOptimisticMessageId(conversationId, clientRequestId, newId);

    // 3. 确保消息在缓存中（如果更新失败，直接追加）
    const cached = store.getCachedMessages(conversationId);
    const messageExists = cached?.messages.some((m) => m.id === newId);
    if (!messageExists) {
      store.appendMessage(conversationId, message);
    }
  },

  /**
   * 标记对话未读
   *
   * 调用方：useMessageCallbacks、taskRestoration
   *
   * @param conversationId - 对话 ID
   */
  markConversationUnread(conversationId: string): void {
    useMessageStore.getState().markConversationUnread(conversationId);
  },
};
