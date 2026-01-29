/**
 * 模型定义和类型
 *
 * 包含所有AI模型的配置和能力定义
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

// 合并所有模型
export const ALL_MODELS: UnifiedModel[] = [
  // 聊天模型
  {
    id: 'gemini-3-flash',
    name: 'Gemini 3 Flash',
    type: 'chat',
    description: '快速响应 | 多模态理解',
    capabilities: {
      // 生成能力
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: false,
      // 理解能力（原生多模态）
      vqa: true, // 图片理解
      videoQA: true, // 视频理解
      audioInput: true, // 音频输入（MP3/WAV/FLAC）
      pdfInput: true, // PDF 文档理解
      // 高级功能
      functionCalling: true, // 工具调用（Agent）
      structuredOutput: true, // 强制 JSON 输出
      thinkingEffort: true, // 可配置推理深度（minimal/low/medium/high）
      streamingResponse: true, // 流式响应
      // 容量限制（参考 Kie.ai 官方文档）
      maxImages: 10, // 最多 10 张图片
      maxFileSize: 20, // 图片 ≤20MB
      maxAudioSize: 25, // 音频 ≤25MB
      maxVideoSize: 100, // 视频 ≤100MB（建议 <30秒）
      maxPDFSize: 50, // PDF ≤50MB/50页
      maxContextTokens: 1000000, // 1M tokens 超长上下文
    },
    credits: 0,
  },
  {
    id: 'gemini-3-pro',
    name: 'Gemini 3 Pro',
    type: 'chat',
    description: '高级推理 | Pro 级多模态',
    capabilities: {
      // 生成能力
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: false,
      // 理解能力（原生多模态）
      vqa: true, // 图片理解
      videoQA: true, // 视频理解
      audioInput: true, // 音频输入
      pdfInput: true, // PDF 文档理解
      // 高级功能
      functionCalling: true, // 工具调用
      structuredOutput: true, // 结构化输出
      thinkingEffort: true, // 可配置推理深度
      streamingResponse: true, // 流式响应
      // 容量限制（与 Flash 相同，Pro 版能力更强但限制相似）
      maxImages: 10,
      maxFileSize: 20,
      maxAudioSize: 25,
      maxVideoSize: 100,
      maxPDFSize: 50,
      maxContextTokens: 1000000, // 1M tokens
    },
    credits: 0,
  },

  // 图像模型
  {
    id: 'google/nano-banana',
    name: 'Nano Banana',
    type: 'image',
    description: '基础文生图',
    capabilities: {
      textToImage: true,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
    },
    credits: 4, // ~$0.02 per image
  },
  {
    id: 'google/nano-banana-edit',
    name: 'Nano Banana Edit',
    type: 'image',
    description: '图像编辑',
    capabilities: {
      textToImage: false,
      imageEditing: true,
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 10, // 支持最多 10 张图片
      maxFileSize: 10, // 单个文件最大 10MB
    },
    credits: 6, // ~¥0.216 per image (图像编辑)
  },
  {
    id: 'nano-banana-pro',
    name: 'Nano Banana Pro',
    type: 'image',
    description: '高级文生图/图生图',
    capabilities: {
      textToImage: true,
      imageEditing: true, // 也支持图像编辑
      imageToVideo: false,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 8, // 支持最多 8 张图片
      maxFileSize: 30, // 单个文件最大 30MB
    },
    credits: { '1K': 18, '2K': 18, '4K': 24 }, // 1K/2K: ~$0.09, 4K: ~$0.12
    supportsResolution: true,
  },

  // 视频生成模型
  {
    id: 'sora-2-text-to-video',
    name: 'Sora 2 Text-to-Video',
    type: 'video',
    description: '文生视频',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: true,
      vqa: false,
      videoQA: false,
    },
    credits: 30, // 10秒基础价格
    videoPricing: {
      '10': 30, // ~¥1.08
      '15': 45, // ~¥1.62
    },
  },
  {
    id: 'sora-2-image-to-video',
    name: 'Sora 2 Image-to-Video',
    type: 'video',
    description: '图生视频',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: true,
      textToVideo: false,
      vqa: false,
      videoQA: false,
      maxImages: 1, // 图生视频只支持 1 张图片
    },
    credits: 30, // 10秒基础价格
    videoPricing: {
      '10': 30, // ~¥1.08
      '15': 45, // ~¥1.62
    },
  },
  {
    id: 'sora-2-pro-storyboard',
    name: 'Sora 2 Pro Storyboard',
    type: 'video',
    description: '专业故事板',
    capabilities: {
      textToImage: false,
      imageEditing: false,
      imageToVideo: false,
      textToVideo: true,
      vqa: false,
      videoQA: false,
    },
    credits: 150, // 10秒基础价格
    videoPricing: {
      '10': 150, // ~¥5.40
      '15': 270, // ~¥9.72
      '25': 270, // ~¥9.72
    },
  },
];

// 根据类型获取模型列表
export function getModelsByType(type: ModelType): UnifiedModel[] {
  return ALL_MODELS.filter((m) => m.type === type);
}

// 根据ID获取模型
export function getModelById(id: string): UnifiedModel | undefined {
  return ALL_MODELS.find((m) => m.id === id);
}

// 根据图片状态筛选可用模型
export function getAvailableModels(hasImage: boolean): UnifiedModel[] {
  // 显示所有模型，让冲突检测来处理不匹配的情况
  // hasImage 参数保留供未来筛选逻辑使用
  void hasImage;
  return ALL_MODELS;
}
