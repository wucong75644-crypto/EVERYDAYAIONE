/**
 * 模型类型定义
 *
 * 所有 AI 模型的接口和能力类型
 */

// 统一模型类型
export type ModelType = 'chat' | 'image' | 'video';

// 模型能力定义
export interface ModelCapabilities {
  // 生成能力
  textToImage: boolean; // 纯文生图
  imageEditing: boolean; // 图生图/编辑
  imageToVideo: boolean; // 图生视频
  textToVideo: boolean; // 文生视频

  // 理解能力
  vqa: boolean; // 视觉问答（图片）
  videoQA: boolean; // 视频问答
  audioInput?: boolean; // 音频输入支持
  pdfInput?: boolean; // PDF 文档支持

  // 高级功能
  functionCalling?: boolean; // 工具调用/函数调用
  structuredOutput?: boolean; // 结构化输出（JSON Schema）
  thinkingEffort?: boolean; // 可配置推理强度
  streamingResponse?: boolean; // 流式响应

  // 容量限制
  maxImages?: number; // 最多支持的图片数量（undefined 表示不支持图片输入）
  maxFileSize?: number; // 单个图片文件最大大小（MB）
  maxAudioSize?: number; // 单个音频文件最大大小（MB）
  maxVideoSize?: number; // 单个视频文件最大大小（MB）
  maxPDFSize?: number; // 单个 PDF 文件最大大小（MB）
  maxContextTokens?: number; // 最大上下文长度（tokens）
}

// 统一模型接口
export interface UnifiedModel {
  id: string;
  name: string;
  type: ModelType;
  description: string;
  capabilities: ModelCapabilities;
  credits: number | Record<string, number>;
  supportsResolution?: boolean;
  videoPricing?: Record<string, number>; // 视频模型的时长定价 { '10': 30, '15': 45, '25': 270 }
}
