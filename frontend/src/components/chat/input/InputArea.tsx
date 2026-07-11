/**
 * 输入区域组件
 *
 * 统一聊天和图像生成界面，根据选择的模型自动判断功能
 */
import { useState, useEffect, useCallback } from 'react';
// lucide-react icons moved to InputControls (AI button now inside input)
import { updateConversation } from '../../../services/conversation';
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
import { logger } from '../../../utils/logger';
import { useFileMention } from '../../../hooks/useFileMention';
import ConflictAlert from './ConflictAlert';
import InputControls from './InputControls';
import UploadErrorBar from './UploadErrorBar';
import { useInputTaskControls } from './useInputTaskControls';
import { useInputSubmission } from './useInputSubmission';
import { useInputExternalEvents } from './useInputExternalEvents';
import { ECOM_TAB_COMPLETIONS, ECOM_TAB_KEYS_SORTED } from './inputCompletions';

interface InputAreaProps {
  conversationId: string | null;
  /** 当前对话保存的模型 ID（用于恢复模型选择） */
  conversationModelId?: string | null;
  /** 当前对话保存的聊天设置（用于恢复设置） */
  conversationChatSettings?: import('../../../services/conversation').ChatSettings | null;
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
  /** 添加单个工作区文件（@ 提及选中时调用） */
  onAddWorkspaceFile?: (file: { name: string; workspace_path: string; cdn_url: string | null; mime_type: string | null; size: number }) => void;
  /** 移除单个工作区文件 */
  onRemoveWorkspaceFile?: (workspacePath: string) => void;
  /** 发送后清空工作区文件队列 */
  onWorkspaceFilesConsumed?: () => void;
  /** 切换工作区视图 */
  onOpenWorkspace?: () => void;
  /** 工作区是否已打开 */
  workspaceOpen?: boolean;
  /** 紧凑模式：工作区打开时取消 max-w 限制 */
  compact?: boolean;
}

export default function InputArea({
  conversationId,
  conversationModelId,
  conversationChatSettings,
  onConversationCreated,
  onMessagePending,
  onMessageSent,
  onModelChange,
  prompt: controlledPrompt,
  onPromptChange: controlledOnPromptChange,
  workspaceFiles = [],
  onAddWorkspaceFile,
  onRemoveWorkspaceFile,
  onWorkspaceFilesConsumed,
  onOpenWorkspace,
  workspaceOpen = false,
  compact = false,
}: InputAreaProps) {
  // 基础状态 — prompt 支持受控和非受控两种模式（向后兼容）
  const [internalPrompt, setInternalPrompt] = useState('');
  const prompt = controlledPrompt ?? internalPrompt;
  const setPrompt = controlledOnPromptChange ?? setInternalPrompt;
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  // 用户积分（用于禁用积分不足的数量选项）
  const userCredits = useAuthStore((s) => s.user?.credits);
  // 设置管理 Hook（图像/视频/聊天参数，含智能模式子模式）
  const {
    imageSettings,
    setImageSetting,
    videoSettings,
    setVideoSetting,
    chatSettings,
    setChatSetting,
    saveSettings: handleSaveSettings,
    resetSettings: handleResetSettings,
  } = useSettingsManager(conversationId, conversationChatSettings);

  // 智能模式子模式：从 chatSettings 获取（对话级持久化）
  const smartSubMode = chatSettings.smartSubMode;
  const setSmartSubMode = useCallback((mode: string) => {
    setChatSetting('smartSubMode', mode as import('../../../hooks/useSettingsManager').SmartSubMode);
  }, [setChatSetting]);

  // 图片上传 Hook
  const {
    images,
    uploadedImageUrls,
    uploadedImages,
    isUploading,
    uploadError: imageUploadError,
    hasImages,
    hasQuotedImage,
    handleImageFiles,
    handleRemoveImage: removeImageById,
    handleRemoveAllImages,
    addQuotedImage,
    clearUploadError,
  } = useImageUpload();

  // 通用文档/数据/文本上传 Hook（非图片走这条）
  const {
    files,
    uploadedFileUrls,
    isUploading: isFileUploading,
    uploadError: fileUploadError,
    hasFiles,
    handleFileUpload,
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

  // @ 文件提及 Hook
  const fileMention = useFileMention();
  // @ 提及选中文件：添加到 workspaceFiles + 用 hook 精准移除 @keyword
  const handleMentionSelect = useCallback((file: { name: string; workspace_path: string; cdn_url: string | null; mime_type: string | null; size: number }) => {
    onAddWorkspaceFile?.(file);
    // consumeMention 用 hook 内部记录的精准 @ 起始位置做替换，不依赖 lastIndexOf
    setPrompt(fileMention.consumeMention(prompt));
  }, [onAddWorkspaceFile, prompt, setPrompt, fileMention.consumeMention]);

  // 构建当前 chat_settings 快照（创建对话时保存）
  const buildChatSettingsPayload = useCallback(() => ({
    smart_sub_mode: chatSettings.smartSubMode,
    deep_think_mode: chatSettings.deepThinkMode,
    thinking_effort: chatSettings.thinkingEffort,
    temperature: chatSettings.temperature,
    top_p: chatSettings.topP,
    top_k: chatSettings.topK,
    max_output_tokens: chatSettings.maxOutputTokens,
    image_aspect_ratio: imageSettings.aspectRatio,
    image_resolution: imageSettings.resolution,
    image_output_format: imageSettings.outputFormat,
    image_num_images: imageSettings.numImages,
    video_frames: videoSettings.frames,
    video_aspect_ratio: videoSettings.aspectRatio,
    video_remove_watermark: videoSettings.removeWatermark,
  }), [chatSettings, imageSettings, videoSettings]);

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

  // 统一上传入口：UploadMenu 按用户原生 file picker 选好的 File[] 在此分流
  // image/* → useImageUpload.handleImageFiles（构造 ImagePart）
  // 其他 → useFileUpload.handleFileUpload（构造 FilePart）
  // 两条 hook 内部都走 /images/upload 或 /files/upload，P0 后已落 上传/{YYYY-MM}/。
  // 注：必须放在 useModelSelection 之后（依赖 selectedModel.capabilities）
  const handleUnifiedFiles = useCallback(
    (incoming: File[]) => {
      if (incoming.length === 0) return;
      const images: File[] = [];
      const docs: File[] = [];
      for (const f of incoming) {
        if (f.type.startsWith('image/')) images.push(f);
        else docs.push(f);
      }
      if (images.length > 0) {
        handleImageFiles(
          images,
          selectedModel.capabilities.maxImages,
          selectedModel.capabilities.maxFileSize,
        );
      }
      if (docs.length > 0) {
        handleFileUpload(docs, selectedModel.capabilities.maxPDFSize);
      }
    },
    [handleImageFiles, handleFileUpload, selectedModel],
  );

  // 实际生效的模型类型：智能模式用子模式，单模型用模型自身类型
  // 电商图模式走专用 EcomImageHandler（和 ImageHandler 同级），不走 ChatHandler
  const isSmart = isSmartModel(selectedModel.id);
  const effectiveModelType: ModelType = isSmart
    ? (smartSubMode.startsWith('image') ? 'image'  // 图生图/文生图/电商图都走 image 路径
      : smartSubMode as ModelType)
    : selectedModel.type;
  const isEcomMode = smartSubMode === 'image-ecom';
  // 切换到非智能模型时重置子模式
  useEffect(() => {
    if (!isSmart) setSmartSubMode('chat');
  }, [isSmart]);

  // 监听图片引用事件（从 AI 生成图片右键菜单触发）
  useEffect(() => {
    const handleQuoteImage = (e: Event) => {
      const { url, thumbnailUrl } = (e as CustomEvent<{ url: string; thumbnailUrl?: string; messageId: string }>).detail;
      addQuotedImage(url, thumbnailUrl);
    };
    window.addEventListener('chat:quote-image', handleQuoteImage);
    return () => window.removeEventListener('chat:quote-image', handleQuoteImage);
  }, [addQuotedImage]);

  // 监听文字引用事件（从用户消息右键菜单触发）
  // 直接把引用文字插到输入框开头：空 prompt 直接铺；非空时加单换行分隔
  useEffect(() => {
    const handleQuoteText = (e: Event) => {
      const { text } = (e as CustomEvent<{ text: string; messageId: string }>).detail;
      if (!text || !text.trim()) return;
      setPrompt(prompt ? `${text}\n${prompt}` : text);
    };
    window.addEventListener('chat:quote-text', handleQuoteText);
    return () => window.removeEventListener('chat:quote-text', handleQuoteText);
  }, [prompt, setPrompt]);

  // 流式状态检测
  const isStreaming = useMessageStore((s) =>
    conversationId ? s.streamingMessages.has(conversationId) : false
  );
  const streamingMessageId = useMessageStore((s) =>
    conversationId ? s.streamingMessages.get(conversationId) ?? null : null
  );

  const { handleStop, sendSteer } = useInputTaskControls({
    conversationId,
    isStreaming,
    streamingMessageId,
  });
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
  const { handleAudioSubmit, handleSubmit } = useInputSubmission({
    conversationId, selectedModel, prompt, setPrompt, audioBlob, clearRecording,
    isSubmitting, setIsSubmitting, setUploadError, setSendError,
    buildChatSettingsPayload, onConversationCreated, onMessageSent,
    handleChatMessage, handleImageGeneration, handleVideoGeneration,
    isEcomMode, effectiveModelType, smartSubMode, isStreaming, sendSteer,
    isUploading, isFileUploading, hasImages, hasFiles,
    uploadedImageUrls, uploadedImages, uploadedFileUrls, workspaceFiles,
    getSendButtonState, handleRemoveAllImages, handleRemoveAllFiles,
    onWorkspaceFilesConsumed,
  });

  // 键盘快捷键（Tab 补全用模块级常量 ECOM_TAB_COMPLETIONS / ECOM_TAB_KEYS_SORTED）
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // @ 提及键盘导航优先拦截
    if (fileMention.showDropdown) {
      const handled = fileMention.handleKeyDown(e);
      if (handled) {
        // Enter 选中当前高亮项
        if (e.key === 'Enter' && fileMention.results[fileMention.activeIndex]) {
          handleMentionSelect(fileMention.results[fileMention.activeIndex]);
        }
        return;
      }
    }
    // Tab 补全（仅电商图模式，用模块级预排序常量避免重建）
    if (e.key === 'Tab' && isEcomMode && prompt.trim()) {
      e.preventDefault();
      for (const key of ECOM_TAB_KEYS_SORTED) {
        if (prompt.endsWith(key)) {
          setPrompt(prompt.slice(0, -key.length) + ECOM_TAB_COMPLETIONS[key]);
          return;
        }
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  };

  useInputExternalEvents({
    conversationId,
    prompt,
    uploadedImageUrls,
    handleImageGeneration,
    handleChatMessage,
  });

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

        {/* 主输入控件 */}
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
          isUploading={isUploading}
          onRemoveImage={handleRemoveImage}
          files={files}
          onRemoveFile={handleRemoveFile}
          workspaceFiles={workspaceFiles}
          onRemoveWorkspaceFile={onRemoveWorkspaceFile}
          onOpenWorkspace={onOpenWorkspace}
          onUnifiedFiles={handleUnifiedFiles}
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
          onSmartSubModeChange={isSmart ? setSmartSubMode : undefined}
          isEnhancing={false}
          onEnhancePrompt={undefined /* v2: 表单提交替代 AI 按钮 */}
          mentionDropdownVisible={fileMention.showDropdown}
          mentionResults={fileMention.results}
          mentionActiveIndex={fileMention.activeIndex}
          mentionLoading={fileMention.loading}
          onMentionSelect={handleMentionSelect}
          onMentionHover={(index: number) => fileMention.setActiveIndex(index)}
          onMentionInputChange={fileMention.handleInputChange}
        />
      </div>
    </div>
  );
}
