/**
 * 输入区域组件
 *
 * 统一聊天和图像生成界面，根据选择的模型自动判断功能
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { createConversation, updateConversation } from '../../../services/conversation';
import { uploadAudio } from '../../../services/audio';
import { useMessageHandlers } from '../../../hooks/useMessageHandlers';
import { useImageUpload } from '../../../hooks/useImageUpload';
import { useFileUpload } from '../../../hooks/useFileUpload';
import { useModelSelection } from '../../../hooks/useModelSelection';
import { useAudioRecording } from '../../../hooks/useAudioRecording';
import { useSettingsManager } from '../../../hooks/useSettingsManager';
import { type UnifiedModel, type ModelType } from '../../../constants/models';
import { isSmartModel } from '../../../constants/smartModel';
import { useMessageStore, type Message } from '../../../stores/useMessageStore';
import { useAuthStore } from '../../../stores/useAuthStore';
import { cancelTaskByMessageId } from '../../../services/message';
import { logger } from '../../../utils/logger';
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
  /** 模型变化时调用（同步给父组件，用于重新生成） */
  onModelChange?: (model: UnifiedModel) => void;
  /** 受控 prompt（状态提升到 Chat.tsx，切换工作区视图时不丢失） */
  prompt?: string;
  /** prompt 变更回调 */
  onPromptChange?: (value: string) => void;
  /** 工作区待发送文件（"插入到聊天"功能） */
  workspaceFiles?: Array<{ name: string; workspace_path: string; cdn_url: string | null; mime_type: string | null; size: number }>;
  /** 移除单个工作区文件 */
  onRemoveWorkspaceFile?: (workspacePath: string) => void;
  /** 发送后清空工作区文件队列 */
  onWorkspaceFilesConsumed?: () => void;
  /** 切换工作区视图 */
  onOpenWorkspace?: () => void;
  /** 上传文件到工作区 */
  onUploadToWorkspace?: (files: File[]) => void;
  /** 工作区是否已打开 */
  workspaceOpen?: boolean;
  /** 紧凑模式：工作区打开时取消 max-w 限制 */
  compact?: boolean;
}

export default function InputArea({
  conversationId,
  conversationModelId,
  onConversationCreated,
  onMessagePending,
  onMessageSent,
  onModelChange,
  prompt: controlledPrompt,
  onPromptChange: controlledOnPromptChange,
  workspaceFiles = [],
  onRemoveWorkspaceFile,
  onWorkspaceFilesConsumed,
  onOpenWorkspace,
  onUploadToWorkspace,
  workspaceOpen = false,
  compact = false,
}: InputAreaProps) {
  // 基础状态 — prompt 支持受控和非受控两种模式（向后兼容）
  const [internalPrompt, setInternalPrompt] = useState('');
  const prompt = controlledPrompt ?? internalPrompt;
  const setPrompt = controlledOnPromptChange ?? setInternalPrompt;
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // 智能模式子模式：聊天/图生图/文生图/视频
  type SmartSubMode = 'chat' | 'image-i2i' | 'image-t2i' | 'video';
  const [smartSubMode, setSmartSubMode] = useState<SmartSubMode>('chat');
  // 实际生效的模型类型：智能模式用子模式，单模型用模型自身类型
  const [sendError, setSendError] = useState<string | null>(null);

  // 用户积分（用于禁用积分不足的数量选项）
  const userCredits = useAuthStore((s) => s.user?.credits);

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
    hasQuotedImage,
    handleImageSelect,
    handleImageDrop,
    handleImagePaste,
    handleRemoveImage: removeImageById,
    handleRemoveAllImages,
    addQuotedImage,
    clearUploadError,
  } = useImageUpload();

  // PDF 文件上传 Hook
  const {
    files,
    uploadedFileUrls,
    isUploading: isFileUploading,
    uploadError: fileUploadError,
    hasFiles,
    handleFileSelect,
    handleRemoveFile,
    handleRemoveAllFiles,
    clearUploadError: clearFileUploadError,
  } = useFileUpload();

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
        logger.error('inputArea', '保存模型选择失败', error);
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
    hasQuotedImage,
    conversationId,
    conversationModelId,
    onAutoSaveModel: handleAutoSaveModel,
  });

  // 实际生效的模型类型：智能模式用子模式，单模型用模型自身类型
  const isSmart = isSmartModel(selectedModel.id);
  const effectiveModelType: ModelType = isSmart
    ? (smartSubMode.startsWith('image') ? 'image' : smartSubMode as ModelType)
    : selectedModel.type;

  // 切换到非智能模型时重置子模式
  useEffect(() => {
    if (!isSmart) setSmartSubMode('chat');
  }, [isSmart]);

  // 监听图片引用事件（从 AI 生成图片右键菜单触发）
  useEffect(() => {
    const handleQuoteImage = (e: Event) => {
      const { url } = (e as CustomEvent<{ url: string; messageId: string }>).detail;
      addQuotedImage(url);
    };
    window.addEventListener('chat:quote-image', handleQuoteImage);
    return () => window.removeEventListener('chat:quote-image', handleQuoteImage);
  }, [addQuotedImage]);

  // 流式状态检测
  const isStreaming = useMessageStore((s) =>
    conversationId ? s.streamingMessages.has(conversationId) : false
  );
  const streamingMessageId = useMessageStore((s) =>
    conversationId ? s.streamingMessages.get(conversationId) ?? null : null
  );

  // 停止生成
  const handleStop = useCallback(() => {
    if (!streamingMessageId || !conversationId) return;

    // 1. 标记消息完成（防止后续 WS 事件重复处理）
    useMessageStore.getState().updateMessage(streamingMessageId, { status: 'completed' });
    // 2. 清理流式状态
    useMessageStore.getState().completeStreaming(conversationId);
    // 3. 后端取消任务（fire-and-forget）
    cancelTaskByMessageId(streamingMessageId).catch((err) => {
      logger.error('inputArea', '取消任务失败', err);
    });
  }, [streamingMessageId, conversationId]);

  // 打断当前执行（用户在 AI 执行中发送新消息）
  const sendSteer = useCallback((message: string) => {
    // 获取当前 streaming 任务的 task_id
    const streamingMsg = streamingMessageId
      ? useMessageStore.getState().getMessage(streamingMessageId)
      : undefined;
    const taskId = streamingMsg?.task_id;
    if (!taskId || !conversationId) return;

    // 通过 CustomEvent 通知 WebSocketContext 发送 user_steer
    window.dispatchEvent(new CustomEvent('chat:user-steer', {
      detail: { taskId, conversationId, message },
    }));
    logger.info('inputArea', '发送打断信号', { taskId, msgLen: message.length });
  }, [streamingMessageId, conversationId]);

  // 对话切换时重置提交状态
  useEffect(() => {
    setIsSubmitting(false);
  }, [conversationId]);

  // 同步 selectedModel 给父组件（用于 MessageArea 重新生成）
  useEffect(() => {
    onModelChange?.(selectedModel);
  }, [selectedModel, onModelChange]);

  // 消息处理 Hook
  const { handleChatMessage, handleImageGeneration, handleVideoGeneration } = useMessageHandlers({
    selectedModel,
    aspectRatio: imageSettings.aspectRatio,
    resolution: imageSettings.resolution,
    outputFormat: imageSettings.outputFormat,
    numImages: imageSettings.numImages,
    videoFrames: videoSettings.frames,
    videoAspectRatio: videoSettings.aspectRatio,
    removeWatermark: videoSettings.removeWatermark,
    thinkingEffort: chatSettings.thinkingEffort,
    deepThinkMode: chatSettings.deepThinkMode,
    permissionMode: chatSettings.permissionMode,
    temperature: chatSettings.temperature,
    topP: chatSettings.topP,
    topK: chatSettings.topK,
    maxOutputTokens: chatSettings.maxOutputTokens,
    onMessagePending,
    onMessageSent,
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

  useEffect(() => {
    if (fileUploadError && !uploadError) {
      setUploadError(fileUploadError);
    }
  }, [fileUploadError, uploadError]);

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
      await handleChatMessage(`[语音消息]`, currentConversationId, [uploadResult.audio_url]);
    } catch (error) {
      logger.error('inputArea', '发送语音消息失败', error);
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

    const anyUploading = isUploading || isFileUploading;
    const hasWorkspaceFiles = workspaceFiles.length > 0;
    const sendButtonState = getSendButtonState(isSubmitting, anyUploading, !!(prompt.trim() || hasImages || hasFiles || hasWorkspaceFiles));
    if (sendButtonState.disabled) return;

    // 图生图模式必须上传图片
    if (smartSubMode === 'image-i2i' && !hasImages) {
      toast.error('图生图模式请先上传参考图片');
      return;
    }

    // 检查全局任务限制
    const taskLimitCheck = useMessageStore.getState().canStartTask();
    if (!taskLimitCheck.allowed) {
      toast.error(taskLimitCheck.reason || '任务队列已满');
      return;
    }

    const messageContent = prompt.trim();

    // 打断：如果 AI 正在执行，先发 steer 信号
    if (isStreaming && messageContent) {
      sendSteer(messageContent);
    }

    // 准备图片 URL 数组：使用服务器 URL（确保图片已上传完成）
    const imageUrls = uploadedImageUrls.length > 0 ? [...uploadedImageUrls] : null;
    // 准备文件数组（PDF 上传 + 工作区插入的文件合并）
    const wsFileMapped = workspaceFiles.map((f) => ({
      url: f.cdn_url || '',
      name: f.name,
      mime_type: f.mime_type || 'application/octet-stream',
      size: f.size,
      workspace_path: f.workspace_path,
    }));
    const mergedFiles = [...uploadedFileUrls, ...wsFileMapped];
    const fileData = mergedFiles.length > 0 ? mergedFiles : null;

    // 立即清空输入（提升响应速度）
    setPrompt('');
    handleRemoveAllImages();  // 30秒后才会清理 ObjectURL
    handleRemoveAllFiles();
    onWorkspaceFilesConsumed?.();  // 清空工作区待发送文件
    setIsSubmitting(true);

    // 发送消息时滚动到底部（用户可能在上方浏览历史）
    window.dispatchEvent(new Event('chat:scroll-to-bottom'));

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
      // 智能模式下用 effectiveModelType（子模式），单模型用模型自身类型
      if (effectiveModelType === 'chat') {
        // 聊天消息：统一使用服务器 URL（确保刷新后图片仍然可见）
        // 注：clientRequestId 由 sendMessage 内部生成，无需传入
        await handleChatMessage(
          messageContent,
          currentConversationId!,
          imageUrls,    // 使用服务器 URL（已上传完成）
          fileData,     // PDF 文件信息
        );
      } else if (effectiveModelType === 'video') {
        await handleVideoGeneration(currentConversationId!, messageContent, imageUrls);
      } else {
        await handleImageGeneration(currentConversationId!, messageContent, imageUrls);
      }
    } catch (error) {
      logger.error('inputArea', '发送消息失败', error);
      setPrompt(messageContent);
      setSendError(error instanceof Error ? error.message : '发送失败，请重试');
      onMessageSent(null);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 键盘快捷键
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // 监听建议按钮点击事件 → 自动发送
  useEffect(() => {
    const handler = async (e: Event) => {
      const text = (e as CustomEvent<{ text: string }>).detail?.text;
      if (!text || !conversationId) return;

      // 清除建议
      useMessageStore.getState().clearSuggestions(conversationId);

      // 滚动到底部
      window.dispatchEvent(new Event('chat:scroll-to-bottom'));

      try {
        await handleChatMessage(text, conversationId);
      } catch (error) {
        logger.error('inputArea', '发送建议失败', error);
      }
    };

    window.addEventListener('chat:send-suggestion', handler);
    return () => window.removeEventListener('chat:send-suggestion', handler);
  }, [conversationId, handleChatMessage]);

  const anyUploadingState = isUploading || isFileUploading;
  const sendButtonState = getSendButtonState(isSubmitting, anyUploadingState, !!(prompt.trim() || hasImages || hasFiles || workspaceFiles.length > 0));

  // 输入变化时清除发送错误状态 + 隐藏建议
  const handlePromptChange = useCallback((value: string) => {
    setPrompt(value);
    if (sendError) setSendError(null);
    // 用户开始输入时清除建议
    if (value && conversationId) {
      useMessageStore.getState().clearSuggestions(conversationId);
    }
  }, [sendError, conversationId]);

  return (
    <div className="bg-surface-card">
      <div className={compact ? "px-4 pb-4" : "max-w-4xl mx-auto px-4 pb-4"}>
        {/* 上传错误提示条 */}
        <UploadErrorBar
          error={uploadError}
          onDismiss={() => {
            setUploadError(null);
            clearUploadError();
            clearFileUploadError();
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
          numImages={imageSettings.numImages}
          onNumImagesChange={(v) => setImageSetting('numImages', v)}
          userCredits={userCredits}
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
          permissionMode={chatSettings.permissionMode}
          onPermissionModeChange={(v) => setChatSetting('permissionMode', v)}
          temperature={chatSettings.temperature}
          onTemperatureChange={(v) => setChatSetting('temperature', v)}
          topP={chatSettings.topP}
          onTopPChange={(v) => setChatSetting('topP', v)}
          topK={chatSettings.topK}
          onTopKChange={(v) => setChatSetting('topK', v)}
          maxOutputTokens={chatSettings.maxOutputTokens}
          onMaxOutputTokensChange={(v) => setChatSetting('maxOutputTokens', v)}
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
          files={files}
          maxPDFSize={selectedModel.capabilities.maxPDFSize}
          onRemoveFile={handleRemoveFile}
          onFileSelect={handleFileSelect}
          workspaceFiles={workspaceFiles}
          onRemoveWorkspaceFile={onRemoveWorkspaceFile}
          onOpenWorkspace={onOpenWorkspace}
          onUploadToWorkspace={onUploadToWorkspace}
          workspaceOpen={workspaceOpen}
          recordingState={recordingState}
          audioBlob={audioBlob}
          audioDuration={audioDuration}
          onStartRecording={startRecording}
          onStopRecording={stopRecording}
          onClearRecording={clearRecording}
          requiresImageUpload={modelConflict?.type === 'requires_image'}
          hasQuotedImage={hasQuotedImage}
          isStreaming={isStreaming}
          onStop={handleStop}
          effectiveModelType={effectiveModelType}
          smartSubMode={isSmart ? smartSubMode : undefined}
          onSmartSubModeChange={isSmart ? (mode: string) => setSmartSubMode(mode as SmartSubMode) : undefined}
        />
      </div>
    </div>
  );
}
