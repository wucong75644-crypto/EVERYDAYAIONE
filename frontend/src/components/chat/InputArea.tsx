/**
 * 输入区域组件
 *
 * 统一聊天和图像生成界面，根据选择的模型自动判断功能
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { createConversation, updateConversation } from '../../services/conversation';
import { type Message } from '../../services/message';
import { uploadAudio } from '../../services/audio';
import { useMessageHandlers } from '../../hooks/useMessageHandlers';
import { useImageUpload } from '../../hooks/useImageUpload';
import { useModelSelection } from '../../hooks/useModelSelection';
import { useAudioRecording } from '../../hooks/useAudioRecording';
import { useSettingsManager } from '../../hooks/useSettingsManager';
import { type UnifiedModel } from '../../constants/models';
import { useTaskStore } from '../../stores/useTaskStore';
import { useChatStore } from '../../stores/useChatStore';
import { generateClientRequestId } from '../../utils/messageIdMapping';
import ConflictAlert from './ConflictAlert';
import InputControls from './InputControls';
import UploadErrorBar from './UploadErrorBar';

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
  onStreamContent?: (text: string, conversationId: string) => void;
  /** AI开始生成时调用（用于创建streaming消息） */
  onStreamStart?: (conversationId: string, model: string) => void;
  /** 模型变化时调用（同步给父组件，用于重新生成） */
  onModelChange?: (model: UnifiedModel) => void;
}

export default function InputArea({
  conversationId,
  conversationModelId,
  onConversationCreated,
  onMessagePending,
  onMessageSent,
  onStreamContent,
  onStreamStart,
  onModelChange,
}: InputAreaProps) {
  // 基础状态
  const [prompt, setPrompt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  // 设置管理 Hook（图像/视频/聊天参数）
  const {
    imageSettings,
    setImageSetting,
    videoSettings,
    setVideoSetting,
    chatSettings,
    setChatSetting,
    saveSettings: handleSaveSettings,
    resetSettings: handleResetSettings,
  } = useSettingsManager();

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

  // 音频录制 Hook
  const {
    recordingState,
    audioBlob,
    audioDuration,
    startRecording,
    stopRecording,
    clearRecording,
    error: audioRecordingError,
  } = useAudioRecording();

  // 自动保存模型到对话的回调
  const handleAutoSaveModel = useCallback((modelId: string) => {
    if (conversationId) {
      updateConversation(conversationId, { model_id: modelId }).catch((error) => {
        console.error('保存模型选择失败:', error);
      });
    }
  }, [conversationId]);

  // 模型选择 Hook（包含对话恢复和智能切换逻辑）
  const {
    selectedModel,
    modelConflict,
    modelJustSwitched,
    availableModels,
    handleUserSelectModel,
    getSendButtonState,
    getEstimatedCredits,
    getModelSelectorLockState,
  } = useModelSelection({
    hasImage: hasImages,
    conversationId,
    conversationModelId,
    onAutoSaveModel: handleAutoSaveModel,
  });

  // 对话切换时重置提交状态
  useEffect(() => {
    setIsSubmitting(false);
  }, [conversationId]);

  // 同步 selectedModel 给父组件（用于 MessageArea 重新生成）
  useEffect(() => {
    onModelChange?.(selectedModel);
  }, [selectedModel, onModelChange]);

  // 获取当前对话标题（用于任务追踪）
  const currentConversationTitle = useChatStore((state) => state.currentConversationTitle);

  // 消息处理 Hook
  const { handleChatMessage, handleImageGeneration, handleVideoGeneration } = useMessageHandlers({
    selectedModel,
    aspectRatio: imageSettings.aspectRatio,
    resolution: imageSettings.resolution,
    outputFormat: imageSettings.outputFormat,
    videoFrames: videoSettings.frames,
    videoAspectRatio: videoSettings.aspectRatio,
    removeWatermark: videoSettings.removeWatermark,
    thinkingEffort: chatSettings.thinkingEffort,
    deepThinkMode: chatSettings.deepThinkMode,
    conversationTitle: currentConversationTitle,
    onMessagePending,
    onMessageSent,
    onStreamContent,
    onStreamStart,
  });

  // 同步上传错误（移到 useEffect 避免渲染期间 setState）
  useEffect(() => {
    if (imageUploadError && !uploadError) {
      setUploadError(imageUploadError);
    }
  }, [imageUploadError, uploadError]);

  useEffect(() => {
    if (audioRecordingError && !uploadError) {
      setUploadError(audioRecordingError);
    }
  }, [audioRecordingError, uploadError]);

  // 包装 handleRemoveImage 以清除错误
  const handleRemoveImage = useCallback((imageId: string) => {
    removeImageById(imageId);
    setUploadError(null);
  }, [removeImageById]);

  // 发送音频消息
  const handleAudioSubmit = async (audioBlob: Blob) => {
    if (isSubmitting) return;

    setIsSubmitting(true);

    try {
      let currentConversationId = conversationId;

      // 如果是新对话，先创建对话（同时保存当前模型）
      if (!currentConversationId) {
        const conversation = await createConversation({
          title: '语音对话',
          model_id: selectedModel.id,
        });
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
    // 如果有音频，调用音频提交处理
    if (audioBlob) {
      await handleAudioSubmit(audioBlob);
      clearRecording();
      return;
    }

    const sendButtonState = getSendButtonState(isSubmitting, isUploading, !!(prompt.trim() || hasImages));
    if (sendButtonState.disabled) return;

    // 检查全局任务限制
    const taskLimitCheck = useTaskStore.getState().canStartTask();
    if (!taskLimitCheck.allowed) {
      toast.error(taskLimitCheck.reason || '任务队列已满');
      return;
    }

    const messageContent = prompt.trim();
    // 准备图片 URL：使用服务器 URL（确保图片已上传完成）
    const combinedImageUrl = uploadedImageUrls.length > 0 ? uploadedImageUrls.join(',') : null;

    // 立即清空输入（提升响应速度）
    setPrompt('');
    handleRemoveAllImages();  // 30秒后才会清理 ObjectURL
    setIsSubmitting(true);

    try {
      const isNewConversation = !conversationId;
      const title = messageContent.slice(0, 20) || '新对话';

      // 获取真实的对话 ID（新对话需先创建）
      let currentConversationId = conversationId;

      if (isNewConversation) {
        // 新对话：必须先创建对话，获取真实 ID
        const conversation = await createConversation({
          title,
          model_id: selectedModel.id,
        });
        currentConversationId = conversation.id;
        // 通知父组件对话已创建
        onConversationCreated(conversation.id, title);
      }

      // 发送消息（使用真实对话 ID）
      if (selectedModel.type === 'chat') {
        // 生成唯一的客户端请求 ID
        const clientRequestId = generateClientRequestId();

        // 聊天消息：统一使用服务器 URL（确保刷新后图片仍然可见）
        await handleChatMessage(
          messageContent,
          currentConversationId!,
          combinedImageUrl,     // 使用服务器 URL（已上传完成）
          clientRequestId,
          false  // 允许 handleChatMessage 创建乐观消息
        );
      } else if (selectedModel.type === 'video') {
        await handleVideoGeneration(messageContent, currentConversationId!, combinedImageUrl);
      } else {
        await handleImageGeneration(messageContent, currentConversationId!, combinedImageUrl);
      }
    } catch (error) {
      console.error('发送消息失败:', error);
      setPrompt(messageContent);
      setSendError(error instanceof Error ? error.message : '发送失败，请重试');
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

  // 输入变化时清除发送错误状态
  const handlePromptChange = useCallback((value: string) => {
    setPrompt(value);
    if (sendError) setSendError(null);
  }, [sendError]);

  return (
    <div className="bg-white">
      <div className="max-w-3xl mx-auto px-4 pb-4">
        {/* 上传错误提示条 */}
        <UploadErrorBar
          error={uploadError}
          onDismiss={() => {
            setUploadError(null);
            clearUploadError();
          }}
        />

        {/* 模型冲突警告条（不显示 requires_image 类型，改用输入框内引导） */}
        <ConflictAlert
          conflict={modelConflict?.type === 'requires_image' ? null : modelConflict}
          onSwitchModel={handleUserSelectModel}
          onRemoveImage={handleRemoveAllImages}
        />

        {/* 主输入控件（包含底部的模型选择器） */}
        <InputControls
          prompt={prompt}
          onPromptChange={handlePromptChange}
          onSubmit={handleSubmit}
          sendError={sendError}
          onAudioSubmit={handleAudioSubmit}
          onKeyDown={handleKeyDown}
          isSubmitting={isSubmitting}
          sendButtonDisabled={sendButtonState.disabled}
          sendButtonTooltip={sendButtonState.tooltip}
          selectedModel={selectedModel}
          availableModels={availableModels}
          modelSelectorLocked={getModelSelectorLockState(isUploading).locked}
          modelSelectorLockTooltip={getModelSelectorLockState(isUploading).tooltip}
          onSelectModel={handleUserSelectModel}
          estimatedCredits={getEstimatedCredits(imageSettings.resolution)}
          creditsHighlight={modelJustSwitched}
          aspectRatio={imageSettings.aspectRatio}
          onAspectRatioChange={(v) => setImageSetting('aspectRatio', v)}
          resolution={imageSettings.resolution}
          onResolutionChange={(v) => setImageSetting('resolution', v)}
          outputFormat={imageSettings.outputFormat}
          onOutputFormatChange={(v) => setImageSetting('outputFormat', v)}
          videoFrames={videoSettings.frames}
          onVideoFramesChange={(v) => setVideoSetting('frames', v)}
          videoAspectRatio={videoSettings.aspectRatio}
          onVideoAspectRatioChange={(v) => setVideoSetting('aspectRatio', v)}
          removeWatermark={videoSettings.removeWatermark}
          onRemoveWatermarkChange={(v) => setVideoSetting('removeWatermark', v)}
          thinkingEffort={chatSettings.thinkingEffort}
          onThinkingEffortChange={(v) => setChatSetting('thinkingEffort', v)}
          deepThinkMode={chatSettings.deepThinkMode}
          onDeepThinkModeChange={(v) => setChatSetting('deepThinkMode', v)}
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
          recordingState={recordingState}
          audioBlob={audioBlob}
          audioDuration={audioDuration}
          onStartRecording={startRecording}
          onStopRecording={stopRecording}
          onClearRecording={clearRecording}
          requiresImageUpload={modelConflict?.type === 'requires_image'}
        />
      </div>
    </div>
  );
}
