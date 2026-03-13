/**
 * 智能模型配置
 *
 * AI 自动选择最佳意图和模型。后续新增工具/API 时在此扩展。
 *
 * 工作流程：
 * 1. 前端发送 model="auto" → 后端千问路由判断意图（chat/image/video/web_search）
 * 2. 路由自动推断 system_prompt（人设）
 * 3. 后端根据意图选择实际工作模型（见 intent_router.py AUTO_MODEL_DEFAULTS）
 */

import { type UnifiedModel } from './models';

/** 智能模型 ID */
export const SMART_MODEL_ID = 'auto';

/** 智能模型定义 */
export const SMART_MODEL: UnifiedModel = {
  id: SMART_MODEL_ID,
  name: '智能',
  type: 'chat',
  description: 'AI 自动选择最佳模型和意图',
  capabilities: {
    // 生成能力（全部开启，实际由路由决定）
    textToImage: true,
    imageEditing: true,
    imageToVideo: true,
    textToVideo: true,
    // 理解能力
    vqa: true,
    videoQA: true,
    audioInput: true,
    pdfInput: true,
    // 高级功能
    functionCalling: true,
    structuredOutput: true,
    thinkingEffort: true,
    streamingResponse: true,
    // 容量限制（取各模型最大值）
    maxImages: 10,
    maxFileSize: 30,
    maxAudioSize: 25,
    maxVideoSize: 100,
    maxPDFSize: 50,
    maxContextTokens: 1000000,
  },
  credits: 0,
};

/** 判断是否为智能模型 */
export function isSmartModel(modelId: string): boolean {
  return modelId === SMART_MODEL_ID;
}
