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
  | FilePart
  | ThinkingPart
  | ToolStepPart
  | ToolResultPart
  | FormPart
  | ChartPart
  | EcomPlanPart
  | InterruptMarkerPart;

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
  /** 工作区文件名（用户上传/引用时填充，AI 生成图不填）—— 后端用于注册 file_path_cache 和 attachments 渲染 */
  name?: string;
  /** 工作区相对路径（如 上传/2026-06/xxx.png）。有值时后端注册 file_path_cache，
   *  file_search 等工具按文件名查询可定位到本地路径。图片在视觉模型多模态注入后无需额外读取工具。 */
  workspace_path?: string;
  size?: number;
  mime_type?: string;
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
  /** 工作区相对路径（有值时后端注册 file_path_cache，AI 可用 file_analyze/code_execute 读取） */
  workspace_path?: string;
}

/** 工具结果内容块（独立渲染，不被主 Agent 文本覆盖） */
export interface ToolResultPart {
  type: 'tool_result';
  tool_name: string;
  text: string;
  files?: Array<{ url: string; name: string; mime_type: string; size?: number }>;
}

/** 思考过程内容块（持久化，对标 Vercel AI SDK reasoning part） */
export interface ThinkingPart {
  type: 'thinking';
  text: string;
  duration_ms?: number;
}

/** 工具调用步骤块（折叠式卡片，对标 Vercel AI SDK tool part） */
export interface ToolStepPart {
  type: 'tool_step';
  tool_name: string;
  tool_call_id: string;
  status: 'running' | 'completed' | 'error' | 'cancelled';
  summary?: string;
  code?: string;
  output?: string;
  elapsed_ms?: number;
  input?: string;
  cancelled_at?: string;
}

/** 中断标记块（数据层，前端不渲染独立卡片，仅用于检测） */
export interface InterruptMarkerPart {
  type: 'interrupt_marker';
  interrupted_at: string;
  reason: 'user_cancel' | 'system_timeout' | 'network_error';
}

/** 表单内容块（聊天内嵌表单，如定时任务创建/修改） */
export interface FormPart {
  type: 'form';
  form_type: string;
  form_id: string;
  title?: string;
  description?: string;
  fields: FormField[];
  submit_text?: string;
  cancel_text?: string;
}

/** 电商图方案卡片内容块（用户确认后触发生成） */
export interface EcomPlanPart {
  type: 'ecom_plan';
  product_insight: string;
  visual_strategy: string;
  images: EcomPlanImage[];
  cost_estimate?: { estimated_credits: number; image_count: number };
}

export interface EcomPlanImage {
  role: string;
  purpose: string;
  title: string;
  subtitle: string;
  prompt: string;
  aspect_ratio: string;
  has_text: boolean;
  image_type: string;
}

/** 交互式图表内容块（ECharts 配置 JSON，前端 ChartBlock 渲染） */
export interface ChartPart {
  type: 'chart';
  option: Record<string, unknown>;
  title?: string;
  chart_type?: string;
}

/** 表单字段定义 */
export interface FormField {
  type: 'text' | 'textarea' | 'select' | 'checkbox_group' | 'number' | 'time' | 'hidden';
  name: string;
  label: string;
  required?: boolean;
  default_value?: string | number | number[] | boolean;
  placeholder?: string;
  options?: Array<{ label: string; value: string }>;
  /** 条件显示：当指定字段等于指定值时才显示此字段 */
  visible_when?: { field: string; value: string };
}

// ============================================================
// 消息类型
// ============================================================

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system';

/** 消息状态 */
export type MessageStatus = 'pending' | 'streaming' | 'completed' | 'failed' | 'interrupted';

/** 消息错误 */
export interface MessageError {
  code: string;
  message: string;
}

/** 生成参数 */
export interface GenerationParams {
  type?: 'chat' | 'image' | 'image_ecom' | 'video' | 'audio';
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
  /** AI 主动沟通：消息交互类型 */
  interaction_type?: 'response' | 'question';
  /** AI 主动沟通：pending_interaction ID（恢复时用） */
  interaction_id?: string;
  /** AI 主动沟通：快捷选项（前端渲染为可点击按钮） */
  interaction_options?: string[];
}

// ============================================================
// 任务类型
// ============================================================

/** 任务状态 */
export interface TaskState {
  taskId: string;
  messageId: string;
  conversationId: string;
  type: 'chat' | 'image' | 'image_ecom' | 'video' | 'audio';
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
