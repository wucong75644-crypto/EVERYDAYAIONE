/**
 * 消息类型定义
 */

/** 消息角色 */
export type MessageRole = 'user' | 'assistant';

/** 消息对象 */
export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  updated_at: string;
}

/** 删除消息请求参数 */
export interface DeleteMessageParams {
  messageId: string;
}

/** 删除消息响应 */
export interface DeleteMessageResponse {
  code: number;
  message: string;
  data: {
    id: string;
    conversation_id: string;
  };
}
