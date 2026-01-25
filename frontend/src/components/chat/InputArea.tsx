/**
 * 输入区域组件
 *
 * 统一聊天和图像生成界面，根据选择的模型自动判断功能
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { createConversation, updateConversation } from '../../services/conversation';
import { type Message } from '../../services/message';
import { type AspectRatio, type ImageResolution, type ImageOutputFormat } from '../../services/image';
import { type VideoFrames, type VideoAspectRatio } from '../../services/video';
import { uploadAudio } from '../../services/audio';
import { useMessageHandlers } from '../../hooks/useMessageHandlers';
import { useImageUpload } from '../../hooks/useImageUpload';
import { useModelSelection } from '../../hooks/useModelSelection';
import { getSavedSettings, saveSettings, resetSettings } from '../../utils/settingsStorage';
import { type UnifiedModel, ALL_MODELS } from '../../constants/models';
import ConflictAlert from './ConflictAlert';
import InputControls from './InputControls';

interface InputAreaProps {
  conversationId: string | null;
  /** 当前对话保存的模型 ID（用于恢复模型选择） */
  conversationModelId?: string | null;
  onConversationCreated: (id: string, title: string) => void;
  /** 消息开始发送时调用（乐观更新） */
  onMessagePending: (message: Message) => void;
  /** 消息发送完成时调用，传递 AI 回复 */
  onMessageSent: (aiMessage?: Message | null) => void;
  /** 流式内容更新时调用 */
  onStreamContent?: (text: string) => void;
}

export default function InputArea({
  conversationId,
  conversationModelId,
  onConversationCreated,
  onMessagePending,
  onMessageSent,
  onStreamContent,
}: InputAreaProps) {
  // 基础状态
  const [prompt, setPrompt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // 加载保存的设置
  const savedSettings = getSavedSettings();

  // 图像生成参数
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>(savedSettings.image.aspectRatio);
  const [resolution, setResolution] = useState<ImageResolution>(savedSettings.image.resolution);
  const [outputFormat, setOutputFormat] = useState<ImageOutputFormat>(savedSettings.image.outputFormat);

  // 视频生成参数
  const [videoFrames, setVideoFrames] = useState<VideoFrames>(savedSettings.video.frames);
  const [videoAspectRatio, setVideoAspectRatio] = useState<VideoAspectRatio>(savedSettings.video.aspectRatio);
  const [removeWatermark, setRemoveWatermark] = useState<boolean>(savedSettings.video.removeWatermark);

  // 聊天模型参数
  const [thinkingEffort, setThinkingEffort] = useState<'minimal' | 'low' | 'medium' | 'high'>(
    savedSettings.chat?.thinkingEffort || 'low'
  );
  const [deepThinkMode, setDeepThinkMode] = useState<boolean>(false);

  // UI 状态
  const [uploadError, setUploadError] = useState<string | null>(null);

  // 图片上传 Hook
  const {
    images,
    uploadedImageUrls,
    isUploading,
    uploadError: imageUploadError,
    hasImages,
    handleImageSelect,
    handleImageDrop,
    handleImagePaste,
    handleRemoveImage: removeImageById,
    handleRemoveAllImages,
    clearUploadError,
  } = useImageUpload();

  // 计算是否有图片（用于模型选择）
  const hasImage = hasImages;

  // 模型选择 Hook（使用真实的图片状态）
  const {
    selectedModel,
    userExplicitChoice,
    setUserExplicitChoice,
    modelConflict,
    setModelConflict,
    modelJustSwitched,
    availableModels,
    handleUserSelectModel,
    switchModel,
    getSendButtonState,
    getEstimatedCredits,
    getModelSelectorLockState,
  } = useModelSelection({ hasImage });

  // 保存上传前的模型（用于恢复）
  const modelBeforeUpload = useRef<UnifiedModel | null>(null);

  // 上一次的对话 ID（用于检测对话切换）
  const prevConversationId = useRef<string | null>(null);
  // 上一次的 conversationModelId（用于检测模型ID变化）
  const prevConversationModelId = useRef<string | null>(null);

  // 恢复对话的模型选择
  useEffect(() => {
    // 对话切换时重置状态，允许自动恢复模型
    if (conversationId !== prevConversationId.current) {
      prevConversationId.current = conversationId;
      prevConversationModelId.current = null; // 重置，以便新对话的 model_id 能被检测到变化
      setUserExplicitChoice(false);
    }

    // 只在 conversationModelId 变化时恢复模型
    // 避免用户主动选择后被覆盖
    if (
      conversationId &&
      conversationModelId &&
      conversationModelId !== prevConversationModelId.current &&
      !userExplicitChoice
    ) {
      prevConversationModelId.current = conversationModelId;

      const savedModel = ALL_MODELS.find((m) => m.id === conversationModelId);
      if (savedModel) {
        switchModel(savedModel, false);
      }
    }
  }, [conversationId, conversationModelId, userExplicitChoice, switchModel, setUserExplicitChoice]);

  // 包装 handleUserSelectModel，添加自动保存逻辑
  const handleModelSelect = useCallback((model: UnifiedModel) => {
    // 调用原始的 handleUserSelectModel
    handleUserSelectModel(model);

    // 只在对话存在时保存
    if (conversationId) {
      updateConversation(conversationId, { model_id: model.id }).catch((error) => {
        console.error('保存模型选择失败:', error);
      });
    }
  }, [conversationId, handleUserSelectModel]);

  // 智能模型切换：上传图片时自动切换到图像编辑模型
  useEffect(() => {
    // 如果用户主动选择过模型，不自动切换
    if (userExplicitChoice) return;

    // 有图片 + 当前是文生图模型 → 切换到编辑模型
    if (hasImage && selectedModel.type === 'image' && !selectedModel.capabilities.imageEditing) {
      // 保存当前模型
      modelBeforeUpload.current = selectedModel;

      // 切换到编辑模型
      const editModel = ALL_MODELS.find((m) => m.id === 'google/nano-banana-edit');
      if (editModel) {
        switchModel(editModel, true);
      }
    }

    // 无图片 + 之前保存过模型 → 恢复原模型
    if (!hasImage && modelBeforeUpload.current) {
      switchModel(modelBeforeUpload.current, true);
      modelBeforeUpload.current = null;
    }
  }, [hasImage, selectedModel, userExplicitChoice, switchModel]);

  // 消息处理 Hook
  const { handleChatMessage, handleImageGeneration, handleVideoGeneration } = useMessageHandlers({
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    videoFrames,
    videoAspectRatio,
    removeWatermark,
    thinkingEffort,
    deepThinkMode,
    onMessagePending,
    onMessageSent,
    onStreamContent,
  });

  // 同步上传错误
  if (imageUploadError && !uploadError) {
    setUploadError(imageUploadError);
  }

  // 包装 handleRemoveImage 以清除错误
  const handleRemoveImage = (imageId: string) => {
    removeImageById(imageId);
    setUploadError(null);
  };

  // 保存当前设置为默认值
  const handleSaveSettings = () => {
    saveSettings({
      image: {
        aspectRatio,
        resolution,
        outputFormat,
      },
      video: {
        frames: videoFrames,
        aspectRatio: videoAspectRatio,
        removeWatermark,
      },
      chat: {
        thinkingEffort,
      },
    });
  };

  // 重置为默认设置
  const handleResetSettings = () => {
    const defaults = resetSettings();
    setAspectRatio(defaults.image.aspectRatio);
    setResolution(defaults.image.resolution);
    setOutputFormat(defaults.image.outputFormat);
    setVideoFrames(defaults.video.frames);
    setVideoAspectRatio(defaults.video.aspectRatio);
    setRemoveWatermark(defaults.video.removeWatermark);
    setThinkingEffort(defaults.chat.thinkingEffort);
  };

  // 发送音频消息
  const handleAudioSubmit = async (audioBlob: Blob) => {
    if (isSubmitting) return;

    setIsSubmitting(true);

    try {
      let currentConversationId = conversationId;

      // 如果是新对话，先创建对话
      if (!currentConversationId) {
        const conversation = await createConversation({ title: '语音对话' });
        currentConversationId = conversation.id;
        onConversationCreated(currentConversationId, '语音对话');
      }

      // 上传音频文件
      const uploadResult = await uploadAudio(audioBlob);

      // 发送消息（将音频 URL 作为附件）
      await handleChatMessage(`[语音消息]`, currentConversationId, uploadResult.audio_url);
    } catch (error) {
      console.error('发送语音消息失败:', error);
      setUploadError(error instanceof Error ? error.message : '语音上传失败');
      onMessageSent(null);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 发送消息
  const handleSubmit = async () => {
    const sendButtonState = getSendButtonState(isSubmitting, isUploading, !!(prompt.trim() || hasImages));
    if (sendButtonState.disabled) return;

    const messageContent = prompt.trim();
    const imageUrls = uploadedImageUrls;
    const firstImageUrl = imageUrls[0] || null; // 向后兼容，取第一张图片
    setPrompt('');
    handleRemoveAllImages();
    setIsSubmitting(true);

    try {
      let currentConversationId = conversationId;

      // 如果是新对话，先创建对话
      if (!currentConversationId) {
        const title = messageContent.slice(0, 20) || '新对话';
        const conversation = await createConversation({ title });
        currentConversationId = conversation.id;
        onConversationCreated(currentConversationId, title);
      }

      // 根据模型类型调用不同的处理函数
      if (selectedModel.type === 'chat') {
        await handleChatMessage(messageContent, currentConversationId, firstImageUrl);
      } else if (selectedModel.type === 'video') {
        await handleVideoGeneration(messageContent, currentConversationId, firstImageUrl);
      } else {
        await handleImageGeneration(messageContent, currentConversationId, firstImageUrl);
      }
    } catch (error) {
      console.error('发送消息失败:', error);
      setPrompt(messageContent);
      onMessageSent(null);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 键盘快捷键
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const sendButtonState = getSendButtonState(isSubmitting, isUploading, !!(prompt.trim() || hasImages));

  return (
    <div className="bg-white">
      <div className="max-w-3xl mx-auto px-4 pb-4">
        {/* 上传错误提示条 */}
        {uploadError && (
          <div className="mb-2 px-3 py-2 bg-red-50 border border-red-200 rounded-lg flex items-start space-x-2 transition-all duration-300 ease-out overflow-hidden">
            <svg className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                clipRule="evenodd"
              />
            </svg>
            <div className="flex-1 text-xs text-red-800">{uploadError}</div>
            <button
              onClick={() => {
                setUploadError(null);
                clearUploadError();
              }}
              className="flex-shrink-0 text-red-600 hover:text-red-800"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}

        {/* 模型冲突警告条 */}
        <ConflictAlert
          conflict={modelConflict}
          onSwitchModel={handleModelSelect}
          onRemoveImage={handleRemoveAllImages}
          onClose={() => setModelConflict(null)}
        />

        {/* 主输入控件（包含底部的模型选择器） */}
        <InputControls
          prompt={prompt}
          onPromptChange={setPrompt}
          onSubmit={handleSubmit}
          onAudioSubmit={handleAudioSubmit}
          onKeyDown={handleKeyDown}
          isSubmitting={isSubmitting}
          sendButtonDisabled={sendButtonState.disabled}
          sendButtonTooltip={sendButtonState.tooltip}
          selectedModel={selectedModel}
          availableModels={availableModels}
          modelSelectorLocked={getModelSelectorLockState(isUploading).locked}
          modelSelectorLockTooltip={getModelSelectorLockState(isUploading).tooltip}
          onSelectModel={handleModelSelect}
          estimatedCredits={getEstimatedCredits(resolution)}
          creditsHighlight={modelJustSwitched}
          aspectRatio={aspectRatio}
          onAspectRatioChange={setAspectRatio}
          resolution={resolution}
          onResolutionChange={setResolution}
          outputFormat={outputFormat}
          onOutputFormatChange={setOutputFormat}
          videoFrames={videoFrames}
          onVideoFramesChange={setVideoFrames}
          videoAspectRatio={videoAspectRatio}
          onVideoAspectRatioChange={setVideoAspectRatio}
          removeWatermark={removeWatermark}
          onRemoveWatermarkChange={setRemoveWatermark}
          thinkingEffort={thinkingEffort}
          onThinkingEffortChange={setThinkingEffort}
          deepThinkMode={deepThinkMode}
          onDeepThinkModeChange={setDeepThinkMode}
          onSaveSettings={handleSaveSettings}
          onResetSettings={handleResetSettings}
          images={images}
          maxImages={selectedModel.capabilities.maxImages}
          maxFileSize={selectedModel.capabilities.maxFileSize}
          isUploading={isUploading}
          onRemoveImage={handleRemoveImage}
          onImageSelect={handleImageSelect}
          onImageDrop={handleImageDrop}
          onImagePaste={handleImagePaste}
        />
      </div>
    </div>
  );
}
