/**
 * 模型选择 Hook
 *
 * 管理模型选择、冲突检测和可用模型过滤
 */

import { useState, useMemo } from 'react';
import { type UnifiedModel, ALL_MODELS, getAvailableModels } from '../constants/models';
import { detectConflict } from '../utils/modelConflict';

interface UseModelSelectionParams {
  hasImage: boolean;
}

export function useModelSelection({ hasImage }: UseModelSelectionParams) {
  const [selectedModel, setSelectedModel] = useState<UnifiedModel>(ALL_MODELS[0]);
  const [userExplicitChoice, setUserExplicitChoice] = useState(false);
  const [modelJustSwitched, setModelJustSwitched] = useState(false);

  // 统一冲突检测 - 使用 useMemo 派生状态，避免在 effect 中 setState
  const modelConflict = useMemo(
    () => detectConflict(selectedModel, hasImage),
    [selectedModel, hasImage]
  );

  /**
   * 切换模型（带高亮动画）
   */
  const switchModel = (model: UnifiedModel, shouldHighlight: boolean = false) => {
    setSelectedModel(model);
    if (shouldHighlight) {
      setModelJustSwitched(true);
      setTimeout(() => setModelJustSwitched(false), 2000);
    }
  };

  /**
   * 用户主动选择模型
   */
  const handleUserSelectModel = (model: UnifiedModel) => {
    setSelectedModel(model);
    setUserExplicitChoice(true); // 用户主动选择，接管控制权
    setModelJustSwitched(true); // 触发积分高亮动画
    setTimeout(() => setModelJustSwitched(false), 2000);
  };

  /**
   * 获取可用模型列表
   */
  const availableModels = getAvailableModels(hasImage);

  /**
   * 获取模型选择器锁定状态
   */
  const getModelSelectorLockState = (isUploading: boolean): { locked: boolean; tooltip: string } => {
    // 上传中
    if (isUploading) {
      return { locked: true, tooltip: '图片上传中，请稍候...' };
    }

    // 不因冲突锁定模型选择器 - 允许用户切换到其他模型来解决冲突
    // 冲突通过 ConflictAlert 组件提示，发送按钮通过 getSendButtonState 禁用

    return { locked: false, tooltip: '选择模型' };
  };

  /**
   * 获取发送按钮状态
   */
  const getSendButtonState = (
    isSubmitting: boolean,
    isUploading: boolean,
    hasContent: boolean
  ): { disabled: boolean; tooltip: string } => {
    // 上传中
    if (isUploading) {
      return { disabled: true, tooltip: '图片上传中...' };
    }

    // 正在提交
    if (isSubmitting) {
      return { disabled: true, tooltip: '发送中...' };
    }

    // 没有内容
    if (!hasContent) {
      return { disabled: true, tooltip: '请输入内容或上传图片' };
    }

    // 硬性冲突
    if (modelConflict && modelConflict.severity === 'critical') {
      return { disabled: true, tooltip: modelConflict.message };
    }

    return { disabled: false, tooltip: '发送' };
  };

  /**
   * 计算预估积分
   */
  const getEstimatedCredits = (resolution: string): string => {
    if (selectedModel.type === 'chat') return '按使用量计费';

    // 视频模型积分（默认10秒）
    if (selectedModel.type === 'video') {
      if (typeof selectedModel.credits === 'number') {
        return `${selectedModel.credits} 积分/10秒`;
      }
      return '40-80 积分/10秒';
    }

    // 如果有图片，显示编辑模型积分
    if (hasImage) {
      return '6 积分/张（图像编辑）';
    }

    // 文生图模型积分
    if (typeof selectedModel.credits === 'number') {
      return `${selectedModel.credits} 积分/张`;
    }
    return `${selectedModel.credits?.[resolution] || 25} 积分/张`;
  };

  return {
    selectedModel,
    setSelectedModel,
    userExplicitChoice,
    setUserExplicitChoice,
    modelConflict,
    modelJustSwitched,
    availableModels,
    switchModel,
    handleUserSelectModel,
    getModelSelectorLockState,
    getSendButtonState,
    getEstimatedCredits,
  };
}
