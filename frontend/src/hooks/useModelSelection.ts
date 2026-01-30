/**
 * 模型选择 Hook
 *
 * 管理模型选择、冲突检测、对话模型恢复和智能模型切换
 */

import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import toast from 'react-hot-toast';
import { type UnifiedModel, ALL_MODELS, getAvailableModels } from '../constants/models';
import { detectConflict } from '../utils/modelConflict';

// ============================================================
// 类型定义
// ============================================================

interface UseModelSelectionParams {
  /** 是否有上传图片 */
  hasImage: boolean;
  /** 当前对话ID（用于检测对话切换） */
  conversationId?: string | null;
  /** 对话保存的模型ID（用于恢复模型选择） */
  conversationModelId?: string | null;
  /** 自动保存模型到对话的回调 */
  onAutoSaveModel?: (modelId: string) => void;
}

// ============================================================
// Hook 实现
// ============================================================

export function useModelSelection({
  hasImage,
  conversationId,
  conversationModelId,
  onAutoSaveModel,
}: UseModelSelectionParams) {
  const [selectedModel, setSelectedModel] = useState<UnifiedModel>(ALL_MODELS[0]);
  const [userExplicitChoice, setUserExplicitChoice] = useState(false);
  const [modelJustSwitched, setModelJustSwitched] = useState(false);

  // 保存上传前的模型（用于恢复）
  const modelBeforeUpload = useRef<UnifiedModel | null>(null);
  // 上一次的对话 ID（用于检测对话切换）
  const prevConversationId = useRef<string | null>(null);
  // 上一次的 conversationModelId（用于检测模型ID变化）
  const prevConversationModelId = useRef<string | null>(null);

  // 统一冲突检测 - 使用 useMemo 派生状态，避免在 effect 中 setState
  const modelConflict = useMemo(
    () => detectConflict(selectedModel, hasImage),
    [selectedModel, hasImage]
  );

  /**
   * 切换模型（带高亮动画）
   */
  const switchModel = useCallback((model: UnifiedModel, shouldHighlight: boolean = false) => {
    setSelectedModel(model);
    if (shouldHighlight) {
      setModelJustSwitched(true);
      setTimeout(() => setModelJustSwitched(false), 2000);
    }
  }, []);

  /**
   * 用户主动选择模型（带自动保存）
   */
  const handleUserSelectModel = useCallback((model: UnifiedModel) => {
    setSelectedModel(model);
    setUserExplicitChoice(true); // 用户主动选择，接管控制权
    setModelJustSwitched(true); // 触发积分高亮动画
    setTimeout(() => setModelJustSwitched(false), 2000);

    // 自动保存模型到对话
    onAutoSaveModel?.(model.id);
  }, [onAutoSaveModel]);

  // ============================================================
  // 对话切换时恢复模型选择
  // ============================================================
  useEffect(() => {
    // 对话切换时重置状态
    if (conversationId !== prevConversationId.current) {
      // 不取消正在进行的请求，支持并发生成（对话A生成中切换到对话B，对话A继续后台生成）
      // conversationId 验证守卫（在 MessageArea.tsx 中）会防止状态污染

      prevConversationId.current = conversationId ?? null;
      prevConversationModelId.current = null; // 重置，以便新对话的 model_id 能被检测到变化
      // 使用 queueMicrotask 延迟状态更新，避免同步 setState
      queueMicrotask(() => setUserExplicitChoice(false));
      // 注意：新对话（conversationId = null）或切换对话时，保持当前 selectedModel 不变
    }

    // 只在有 conversationModelId 时恢复模型
    // model_id 为 null 时保持当前选择不变（用户偏好连续性）
    if (
      conversationId &&
      conversationModelId &&
      conversationModelId !== prevConversationModelId.current &&
      !userExplicitChoice
    ) {
      prevConversationModelId.current = conversationModelId;

      const savedModel = ALL_MODELS.find((m) => m.id === conversationModelId);
      // 使用 queueMicrotask 延迟状态更新
      queueMicrotask(() => {
        if (savedModel) {
          switchModel(savedModel, false);
        } else {
          // 边界情况：模型已下架/不存在，降级到默认模型
          switchModel(ALL_MODELS[0], false);
          toast('该对话使用的模型已下架，已切换为默认模型', { icon: 'ℹ️' });
        }
      });
    }
  }, [conversationId, conversationModelId, userExplicitChoice, switchModel]);

  // ============================================================
  // 智能模型切换：上传图片时自动切换到图像编辑模型
  // ============================================================
  useEffect(() => {
    // 如果用户主动选择过模型，不自动切换
    if (userExplicitChoice) return;

    // 有图片 + 当前是文生图模型 → 切换到编辑模型
    if (hasImage && selectedModel.type === 'image' && !selectedModel.capabilities.imageEditing) {
      // 保存当前模型
      modelBeforeUpload.current = selectedModel;

      // 切换到编辑模型 - 使用 queueMicrotask 延迟状态更新
      const editModel = ALL_MODELS.find((m) => m.id === 'google/nano-banana-edit');
      if (editModel) {
        queueMicrotask(() => switchModel(editModel, true));
      }
    }

    // 无图片 + 之前保存过模型 → 恢复原模型
    if (!hasImage && modelBeforeUpload.current) {
      const modelToRestore = modelBeforeUpload.current;
      modelBeforeUpload.current = null;
      queueMicrotask(() => switchModel(modelToRestore, true));
    }
  }, [hasImage, selectedModel, userExplicitChoice, switchModel]);

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
      // 对于需要图片的模型，使用更友好的提示
      if (modelConflict.type === 'requires_image') {
        return { disabled: true, tooltip: '请先上传图片再发送' };
      }
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
