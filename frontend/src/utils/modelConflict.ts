/**
 * 模型冲突检测工具
 *
 * 检测模型选择与当前状态（如是否有图片）的冲突
 */

import { type UnifiedModel, ALL_MODELS } from '../constants/models';

// 冲突严重程度
export type ConflictSeverity = 'critical' | 'warning' | 'info';

// 模型冲突类型
export interface ModelConflict {
  severity: ConflictSeverity;
  type: 'no_image_support' | 'requires_image' | 'vqa_suggestion';
  message: string;
  suggestedModel?: UnifiedModel;
  actions: {
    switchModel?: UnifiedModel;
    removeImage?: boolean;
  };
}

/**
 * 检测模型与当前状态的冲突
 *
 * @param model 当前选中的模型
 * @param hasImage 是否有上传的图片
 * @returns 冲突信息，如果没有冲突返回 null
 */
export function detectConflict(model: UnifiedModel, hasImage: boolean): ModelConflict | null {
  // 硬性冲突 1：文生图模型 + 有图片（模型不支持图片输入）
  if (hasImage && model.type === 'image' && !model.capabilities.imageEditing) {
    return {
      severity: 'critical',
      type: 'no_image_support',
      message: `${model.name} 不支持图片输入，无法发送`,
      suggestedModel: ALL_MODELS.find((m) => m.id === 'google/nano-banana-edit')!,
      actions: {
        switchModel: ALL_MODELS.find((m) => m.id === 'google/nano-banana-edit')!,
        removeImage: true,
      },
    };
  }

  // 硬性冲突 2：图像编辑模型 + 无图片（模型需要图片）
  if (!hasImage && model.type === 'image' && model.capabilities.imageEditing) {
    return {
      severity: 'critical',
      type: 'requires_image',
      message: `${model.name} 需要上传图片才能使用`,
      actions: {},
    };
  }

  // 硬性冲突 3：图生视频模型 + 无图片（模型需要图片）
  if (!hasImage && model.type === 'video' && model.capabilities.imageToVideo) {
    return {
      severity: 'critical',
      type: 'requires_image',
      message: `${model.name} 需要上传图片才能使用`,
      actions: {},
    };
  }

  // VQA（视觉问答）是聊天模型的正常功能，无需提示
  return null;
}
