/**
 * WebSocket 相关类型定义
 */

import type { WSMessage, WSMessageType, ConnectionState } from '../hooks/useWebSocket';
import type { Message } from '../stores/useMessageStore';

/**
 * 操作上下文（用于 WebSocket 完成时回调）
 *
 * 设计目的：
 * 1. 解决重新生成时的回调问题（onComplete 需要传递给 WebSocket 处理器）
 * 2. 统一聊天和媒体完成的回调机制
 */
export interface OperationContext {
  /** 消息类型 */
  type: 'chat' | 'image' | 'video' | 'audio';
  /** 操作类型 */
  operation: 'send' | 'regenerate' | 'retry';
  /** 对话 ID */
  conversationId: string;
  /** 完成回调 */
  onComplete: (finalMessage: Message) => void;
  /** 流式内容回调（仅聊天） */
  onStreamChunk?: (chunk: string, accumulated: string) => void;
  /** 错误回调 */
  onError?: (error: Error) => void;
}

export interface WebSocketContextValue {
  connectionState: ConnectionState;
  isConnected: boolean;
  subscribe: (type: WSMessageType, handler: (msg: WSMessage) => void) => () => void;
  subscribeTask: (taskId: string, lastIndex?: number) => void;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
  /** 订阅任务并维护 taskId -> conversationId 映射（用于发送消息后订阅） */
  subscribeTaskWithMapping: (taskId: string, conversationId: string) => void;
  /**
   * 注册操作上下文
   *
   * 在调用后端 API 成功后，注册操作上下文供 WebSocket 完成时回调。
   * 主要用于重新生成场景，确保 onComplete 等回调能被正确触发。
   */
  registerOperation: (taskId: string, context: OperationContext) => void;
}
