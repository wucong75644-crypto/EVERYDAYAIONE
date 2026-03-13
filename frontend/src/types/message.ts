/**
 * 消息相关类型定义
 *
 * 从 useMessageStore 提取，提供统一的消息类型接口。
 */

// ============================================================
// 内容部件类型
// ============================================================

/** 内容部件类型（OpenAI 风格） */
export type ContentPart =
  | TextPart
  | ImagePart
  | VideoPart
  | AudioPart
  | FilePart;

export interface TextPart {
  type: 'text';
  text: string;
}

export interface ImagePart {
  type: 'image';
  url: string | null;
  width?: number;
  height?: number;
  alt?: string;
  failed?: boolean;
  error?: string;
}

export interface VideoPart {
  type: 'video';
  url: string;
  duration?: number;
  thumbnail?: string;
}

export interface AudioPart {
  type: 'audio';
  url: string;
  duration?: number;
  transcript?: string;
}

export interface FilePart {
  type: 'file';
  url: string;
  name: string;
  mime_type: string;
  size?: number;
}

// ============================================================
// 消息类型
// ============================================================

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system';

/** 消息状态 */
export type MessageStatus = 'pending' | 'streaming' | 'completed' | 'failed';

/** 消息错误 */
export interface MessageError {
  code: string;
  message: string;
}

/** 生成参数 */
export interface GenerationParams {
  type?: 'chat' | 'image' | 'video' | 'audio';
  model?: string;
  /** 思考过程内容（持久化在 generation_params 中） */
  thinking_content?: string;
  [key: string]: unknown;
}

/** 统一消息模型 */
export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: ContentPart[];
  status: MessageStatus;
  task_id?: string;
  generation_params?: GenerationParams;
  credits_cost?: number;
  error?: MessageError;
  created_at: string;
  updated_at?: string;
  client_request_id?: string;
  is_error?: boolean;
}

// ============================================================
// 任务类型
// ============================================================

/** 任务状态 */
export interface TaskState {
  taskId: string;
  messageId: string;
  conversationId: string;
  type: 'chat' | 'image' | 'video' | 'audio';
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  createdAt: number;
  error?: string;
}

/** 聊天任务 */
export interface ChatTask {
  conversationId: string;
  conversationTitle: string;
  status: 'pending' | 'streaming' | 'error';
  startTime: number;
  content?: string;
}

/** 媒体任务 */
export interface MediaTask {
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  type: 'image' | 'video';
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'error';
  startTime: number;
  placeholderId: string;
}

// ============================================================
// 对话和缓存类型
// ============================================================

/** 对话信息 */
export interface Conversation {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: string;
}

/** 消息缓存条目 */
export interface MessageCacheEntry {
  messages: Message[];
  hasMore: boolean;
  lastFetchedAt: number;
  isSending?: boolean;
}

/** 完成通知 */
export interface CompletedNotification {
  id: string;
  conversationId: string;
  conversationTitle: string;
  type: 'chat' | 'image' | 'video';
  isRead: boolean;
  timestamp: number;
}

// ============================================================
// API 类型（兼容旧格式）
// ============================================================

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
