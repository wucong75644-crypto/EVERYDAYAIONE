/**
 * 输入控制组件（重构版）
 *
 * 布局：图片预览 → 输入框 → 工具栏
 * 工具栏：左侧（模型/设置/深度思考） | 右侧（计费/上传/发送或语音）
 */

import { useState, useRef, useEffect } from 'react';
import { m } from 'framer-motion';
import { Send, Square, Settings, Upload, Brain, Paperclip, FolderOpen } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { SOFT_SPRING } from '../../../utils/motion';
import { getFileIcon } from '../../../utils/fileUtils';
import {
  type UnifiedModel,
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
  type ImageCount,
  type VideoFrames,
  type VideoAspectRatio,
} from '../../../constants/models';
import ImagePreview from '../media/ImagePreview';
import FilePreview from '../media/FilePreview';
import AudioPreview from '../media/AudioPreview';
import ModelSelector from './ModelSelector';
import AdvancedSettingsMenu from './AdvancedSettingsMenu';
import UploadMenu from './UploadMenu';
import AudioRecorder from './AudioRecorder';
import { type UploadedImage } from '../../../hooks/useImageUpload';
import { type UploadedFile } from '../../../hooks/useFileUpload';
import { type RecordingState } from '../../../hooks/useAudioRecording';
import { useDragDropUpload } from '../../../hooks/useDragDropUpload';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../../constants/animations';

interface InputControlsProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: () => void;
  onAudioSubmit?: (audioBlob: Blob) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  isSubmitting: boolean;
  sendButtonDisabled: boolean;
  sendButtonTooltip: string;
  // 音频录制相关
  recordingState: RecordingState;
  audioBlob: Blob | null;
  audioDuration: number;
  onStartRecording: () => Promise<void>;
  onStopRecording: () => void;
  onClearRecording: () => void;
  selectedModel: UnifiedModel;
  availableModels: UnifiedModel[];
  modelSelectorLocked: boolean;
  modelSelectorLockTooltip: string;
  onSelectModel: (model: UnifiedModel) => void;
  estimatedCredits: string;
  creditsHighlight: boolean;
  aspectRatio: AspectRatio;
  onAspectRatioChange: (ratio: AspectRatio) => void;
  resolution: ImageResolution;
  onResolutionChange: (res: ImageResolution) => void;
  outputFormat: ImageOutputFormat;
  onOutputFormatChange: (format: ImageOutputFormat) => void;
  numImages: ImageCount;
  onNumImagesChange: (count: ImageCount) => void;
  userCredits?: number;
  videoFrames: VideoFrames;
  onVideoFramesChange: (frames: VideoFrames) => void;
  videoAspectRatio: VideoAspectRatio;
  onVideoAspectRatioChange: (ratio: VideoAspectRatio) => void;
  removeWatermark: boolean;
  onRemoveWatermarkChange: (remove: boolean) => void;
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  onThinkingEffortChange?: (effort: 'minimal' | 'low' | 'medium' | 'high') => void;
  deepThinkMode?: boolean;
  onDeepThinkModeChange?: (enabled: boolean) => void;
  temperature?: number;
  onTemperatureChange?: (value: number) => void;
  topP?: number;
  onTopPChange?: (value: number) => void;
  topK?: number;
  onTopKChange?: (value: number) => void;
  maxOutputTokens?: number;
  onMaxOutputTokensChange?: (value: number) => void;
  onSaveSettings: () => void;
  onResetSettings: () => void;
  images: UploadedImage[];
  maxImages?: number;
  maxFileSize?: number;
  isUploading: boolean;
  onRemoveImage: (imageId: string) => void;
  onImageSelect: (e: React.ChangeEvent<HTMLInputElement>, maxImages?: number, maxFileSize?: number) => void;
  onImageDrop: (files: FileList, maxImages?: number, maxFileSize?: number) => void;
  onImagePaste: (e: ClipboardEvent, maxImages?: number, maxFileSize?: number) => void;
  /** PDF 文件列表 */
  files: UploadedFile[];
  /** PDF 最大大小（MB） */
  maxPDFSize?: number;
  /** 删除 PDF 文件 */
  onRemoveFile: (fileId: string) => void;
  /** 选择 PDF 文件 */
  onFileSelect: (e: React.ChangeEvent<HTMLInputElement>, maxSizeMB?: number) => void;
  /** 工作区待发送文件 */
  workspaceFiles?: Array<{ name: string; workspace_path: string; cdn_url: string | null; mime_type: string | null; size: number }>;
  /** 移除工作区文件 */
  onRemoveWorkspaceFile?: (workspacePath: string) => void;
  /** 切换工作区视图（开/关） */
  onOpenWorkspace?: () => void;
  /** 上传文件到工作区 */
  onUploadToWorkspace?: (files: File[]) => void;
  /** 工作区是否已打开（用于 toggle 按钮状态） */
  workspaceOpen?: boolean;
  /** 是否需要上传图片（用于显示引导提示） */
  requiresImageUpload?: boolean;
  /** 发送错误信息（用于显示错误状态） */
  sendError?: string | null;
  /** 是否有引用图片（用于切换 placeholder） */
  hasQuotedImage?: boolean;
  /** 是否正在流式生成 */
  isStreaming?: boolean;
  /** 停止生成回调 */
  onStop?: () => void;
}

export default function InputControls(props: InputControlsProps) {
  const {
    prompt, onPromptChange, onSubmit, onAudioSubmit, onKeyDown,
    isSubmitting, sendButtonDisabled, sendButtonTooltip,
    selectedModel, availableModels, modelSelectorLocked, modelSelectorLockTooltip, onSelectModel,
    estimatedCredits, creditsHighlight,
    aspectRatio, onAspectRatioChange, resolution, onResolutionChange, outputFormat, onOutputFormatChange,
    numImages, onNumImagesChange, userCredits,
    videoFrames, onVideoFramesChange, videoAspectRatio, onVideoAspectRatioChange,
    removeWatermark, onRemoveWatermarkChange, thinkingEffort, onThinkingEffortChange,
    deepThinkMode, onDeepThinkModeChange,
    temperature, onTemperatureChange, topP, onTopPChange, topK, onTopKChange,
    maxOutputTokens, onMaxOutputTokensChange,
    onSaveSettings, onResetSettings,
    images, maxImages, maxFileSize, onRemoveImage, onImageSelect, onImageDrop, onImagePaste,
    files, maxPDFSize, onRemoveFile, onFileSelect,
    workspaceFiles = [], onRemoveWorkspaceFile, onOpenWorkspace, onUploadToWorkspace, workspaceOpen = false,
    recordingState, audioBlob, audioDuration, onStartRecording, onStopRecording, onClearRecording,
    requiresImageUpload = false, sendError, hasQuotedImage = false,
    isStreaming = false, onStop,
  } = props;

  const [showUploadMenu, setShowUploadMenu] = useState(false);
  const [uploadMenuClosing, setUploadMenuClosing] = useState(false);
  const [showAdvancedDropdown, setShowAdvancedDropdown] = useState(false);
  const [advancedDropdownClosing, setAdvancedDropdownClosing] = useState(false);
  // 上传按钮发光动效状态
  const [uploadButtonGlowing, setUploadButtonGlowing] = useState(false);

  // 当需要上传图片时，触发发光动效（持续2秒）
  useEffect(() => {
    if (requiresImageUpload) {
      // 使用 queueMicrotask 延迟状态更新，避免同步 setState
      queueMicrotask(() => setUploadButtonGlowing(true));
      const timer = setTimeout(() => setUploadButtonGlowing(false), 2000);
      return () => clearTimeout(timer);
    }
    // 当 requiresImageUpload 变为 false 时，在 cleanup 中重置状态
    return () => {
      setUploadButtonGlowing(false);
    };
  }, [requiresImageUpload]);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pdfFileInputRef = useRef<HTMLInputElement>(null);
  const uploadMenuRef = useRef<HTMLDivElement>(null);
  const advancedMenuRef = useRef<HTMLDivElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const { isDragging } = useDragDropUpload({ dropZoneRef, textareaRef, onImageDrop, onImagePaste, maxImages, maxFileSize });

  // 自动调整文本框高度（最多5行，约120px）
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto';
      const lineHeight = 24; // 约等于 text-base 的行高
      const maxLines = 5;
      const maxHeight = lineHeight * maxLines;
      const newHeight = Math.min(textarea.scrollHeight, maxHeight);
      textarea.style.height = `${newHeight}px`;
    }
  }, [prompt]);

  // 关闭上传菜单（带动画）
  const closeUploadMenu = () => {
    setUploadMenuClosing(true);
    setTimeout(() => {
      setShowUploadMenu(false);
      setUploadMenuClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  // 关闭高级设置菜单（带动画）
  const closeAdvancedDropdown = () => {
    setAdvancedDropdownClosing(true);
    setTimeout(() => {
      setShowAdvancedDropdown(false);
      setAdvancedDropdownClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (uploadMenuRef.current && !uploadMenuRef.current.contains(e.target as Node)) {
        if (showUploadMenu) {
          closeUploadMenu();
        }
      }
      if (advancedMenuRef.current && !advancedMenuRef.current.contains(e.target as Node)) {
        if (showAdvancedDropdown) {
          closeAdvancedDropdown();
        }
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showUploadMenu, showAdvancedDropdown]);

  // 判断条件
  const supportsDeepThinking = selectedModel.capabilities.thinkingEffort === true;
  const hasContent = prompt.trim().length > 0 || images.length > 0 || files.length > 0 || workspaceFiles.length > 0;
  const canSubmit = !sendButtonDisabled && (hasContent || audioBlob);

  // 发送/语音按钮互斥显示
  const showSendButton = hasContent || audioBlob;
  const showVoiceButton = !showSendButton && selectedModel.capabilities.audioInput && onAudioSubmit;

  return (
    <div
      ref={dropZoneRef}
      className={cn(
        'relative border rounded-2xl bg-surface-card shadow-sm transition-all',
        sendError && 'border-error border-2 shadow-md',
        !sendError && isDragging && 'border-accent border-2 bg-accent-light shadow-lg',
        !sendError && !isDragging && 'border-border-default hover:shadow-md',
      )}
    >
      {/* 拖拽提示 */}
      {isDragging && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-accent-light/95 rounded-2xl pointer-events-none">
          <div className="text-center">
            <Upload className="w-12 h-12 text-accent mx-auto mb-2" />
            <p className="text-lg font-medium text-accent">拖放图片到这里</p>
            <p className="text-sm text-accent">支持 PNG, JPG, GIF, PDF</p>
          </div>
        </div>
      )}

      <div className="px-3 pt-2 pb-2">
        {/* 图片预览区域（输入框顶部） */}
        {images.length > 0 && (
          <div className="mb-2">
            <ImagePreview images={images} onRemove={onRemoveImage} />
          </div>
        )}

        {/* PDF 文件预览区域 */}
        {files.length > 0 && (
          <FilePreview files={files} onRemove={onRemoveFile} />
        )}

        {/* 工作区文件预览区域（"插入到聊天"的文件） */}
        {workspaceFiles.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {workspaceFiles.map((wf) => (
              <div
                key={wf.workspace_path}
                className="relative flex items-center gap-2 rounded-lg border border-[var(--s-accent)] bg-[var(--s-accent-soft)] px-3 py-2 text-sm"
              >
                <span className="text-base shrink-0">{getFileIcon(wf.name)}</span>
                <span className="truncate max-w-[160px] font-medium text-[var(--s-text-primary)]">{wf.name}</span>
                {onRemoveWorkspaceFile && (
                  <button
                    onClick={() => onRemoveWorkspaceFile(wf.workspace_path)}
                    className="shrink-0 rounded p-0.5 text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)] transition-colors"
                    title="移除"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 音频预览区域 */}
        {audioBlob && (
          <AudioPreview
            audioURL={URL.createObjectURL(audioBlob)}
            recordingTime={audioDuration}
            onClear={onClearRecording}
          />
        )}

        {/* 输入区域 */}
        <textarea
          ref={textareaRef}
          name="chat-input"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={hasQuotedImage ? '描述你想要的修改...' : requiresImageUpload ? '该模型需要先上传图片才能生成哦～' : '发送消息...'}
          className="w-full resize-none border-none outline-none bg-transparent text-text-primary placeholder:text-text-disabled text-base leading-6 pt-2 pb-1 min-h-[44px] max-h-[120px] overflow-y-auto"
          rows={1}
          disabled={isSubmitting}
        />

        {/* 底部工具栏 */}
        <div className="flex items-center justify-between mt-1">
          {/* 左侧：模型选择器、设置、深度思考 */}
          <div className="flex items-center space-x-1">
            {/* 模型选择器 */}
            <ModelSelector
              selectedModel={selectedModel}
              availableModels={availableModels}
              onSelectModel={onSelectModel}
              locked={modelSelectorLocked}
              lockTooltip={modelSelectorLockTooltip}
            />

            {/* 高级设置 */}
            <div ref={advancedMenuRef} className="relative">
              <button
                onClick={() => setShowAdvancedDropdown(!showAdvancedDropdown)}
                className="p-2 text-text-tertiary hover:text-text-primary hover:bg-hover rounded-lg transition-base"
                title="高级设置"
              >
                <Settings className="w-4 h-4" />
              </button>
              {showAdvancedDropdown && (
                <AdvancedSettingsMenu
                  closing={advancedDropdownClosing}
                  selectedModel={selectedModel}
                  aspectRatio={aspectRatio}
                  onAspectRatioChange={onAspectRatioChange}
                  resolution={resolution}
                  onResolutionChange={onResolutionChange}
                  outputFormat={outputFormat}
                  onOutputFormatChange={onOutputFormatChange}
                  numImages={numImages}
                  onNumImagesChange={onNumImagesChange}
                  userCredits={userCredits}
                  videoFrames={videoFrames}
                  onVideoFramesChange={onVideoFramesChange}
                  videoAspectRatio={videoAspectRatio}
                  onVideoAspectRatioChange={onVideoAspectRatioChange}
                  removeWatermark={removeWatermark}
                  onRemoveWatermarkChange={onRemoveWatermarkChange}
                  thinkingEffort={thinkingEffort}
                  onThinkingEffortChange={onThinkingEffortChange}
                  temperature={temperature}
                  onTemperatureChange={onTemperatureChange}
                  topP={topP}
                  onTopPChange={onTopPChange}
                  topK={topK}
                  onTopKChange={onTopKChange}
                  maxOutputTokens={maxOutputTokens}
                  onMaxOutputTokensChange={onMaxOutputTokensChange}
                  onSave={onSaveSettings}
                  onReset={onResetSettings}
                  onClose={closeAdvancedDropdown}
                />
              )}
            </div>

            {/* 深度思考按钮（仅支持的模型显示） */}
            {supportsDeepThinking && onDeepThinkModeChange && (
              <button
                onClick={() => onDeepThinkModeChange(!deepThinkMode)}
                className={cn(
                  'flex items-center space-x-1 px-2 py-1.5 rounded-lg text-sm transition-base',
                  deepThinkMode
                    ? 'bg-accent-light text-accent hover:bg-accent-light/80'
                    : 'text-text-tertiary hover:text-text-primary hover:bg-hover',
                )}
                title={deepThinkMode ? '深度思考已开启' : '开启深度思考'}
              >
                <Brain className="w-4 h-4" />
                <span className="hidden sm:inline">深度思考</span>
              </button>
            )}

            {/* 工作区按钮（toggle：点击开/关） */}
            {onOpenWorkspace && (
              <button
                onClick={onOpenWorkspace}
                className={cn(
                  'flex items-center space-x-1 px-2 py-1.5 rounded-lg text-sm transition-base',
                  workspaceOpen
                    ? 'bg-accent-light text-accent hover:bg-accent-light/80'
                    : 'text-text-tertiary hover:text-text-primary hover:bg-hover',
                )}
                title={workspaceOpen ? '退出工作区' : '打开工作区'}
              >
                <FolderOpen className="w-4 h-4" />
                <span className="hidden sm:inline">工作区</span>
              </button>
            )}
          </div>

          {/* 右侧：计费提示、上传、发送/语音 */}
          <div className="flex items-center space-x-2">
            {/* 计费提示（无边框背景，颜色渐变动画） */}
            <span
              className={cn(
                'text-xs transition-colors duration-1000 ease-out',
                creditsHighlight ? 'text-warning' : 'text-text-disabled',
              )}
            >
              {estimatedCredits}
            </span>

            {/* 上传按钮（始终显示，UploadMenu 内部根据模型能力显示不同选项） */}
            <div ref={uploadMenuRef} className="relative">
              <button
                onClick={() => setShowUploadMenu(!showUploadMenu)}
                className={cn(
                  'p-2 rounded-lg transition-all',
                  uploadButtonGlowing && 'text-error animate-breathe',
                  !uploadButtonGlowing && 'text-text-tertiary hover:text-text-primary hover:bg-hover',
                )}
                title={requiresImageUpload ? '点击上传图片' : '上传文件'}
              >
                <Paperclip className="w-4 h-4" />
              </button>
              <UploadMenu
                visible={showUploadMenu}
                closing={uploadMenuClosing}
                selectedModel={selectedModel}
                onImageUpload={() => fileInputRef.current?.click()}
                onFileUpload={() => pdfFileInputRef.current?.click()}
                onUploadToWorkspace={onUploadToWorkspace}
                onClose={closeUploadMenu}
              />
            </div>

            {/* 停止按钮（生成中显示，替换所有其他按钮） */}
            {isStreaming && onStop ? (
              <button
                onClick={onStop}
                className="p-2.5 rounded-full bg-error text-text-on-accent hover:bg-error/90 shadow-md hover:shadow-lg transition-all"
                title="停止生成"
              >
                <Square className="w-4 h-4 fill-current" />
              </button>
            ) : showSendButton ? (
              <m.button
                onClick={onSubmit}
                disabled={!canSubmit || isSubmitting}
                title={sendError || sendButtonTooltip}
                // V3：发送按钮 spring hover/tap 反馈（苹果级触感）
                whileHover={canSubmit && !isSubmitting ? { scale: 1.08, y: -1 } : undefined}
                whileTap={canSubmit && !isSubmitting ? { scale: 0.92 } : undefined}
                transition={SOFT_SPRING}
                className={cn(
                  'p-2.5 rounded-full',
                  'transition-colors duration-[var(--a-duration-normal)]',
                  sendError && 'bg-error text-text-on-accent hover:bg-error/90 shadow-md hover:shadow-lg',
                  !sendError && canSubmit && !isSubmitting && 'bg-accent text-text-on-accent hover:bg-accent-hover shadow-md hover:shadow-lg',
                  !sendError && (!canSubmit || isSubmitting) && 'bg-active text-text-disabled cursor-not-allowed',
                )}
              >
                <Send className="w-4 h-4" />
              </m.button>
            ) : showVoiceButton ? (
              <AudioRecorder
                isRecording={recordingState === 'recording'}
                recordingTime={audioDuration}
                audioURL={audioBlob ? URL.createObjectURL(audioBlob) : null}
                onStartRecording={onStartRecording}
                onStopRecording={onStopRecording}
                onClearAudio={onClearRecording}
                disabled={isSubmitting}
              />
            ) : (
              <button
                type="button"
                disabled={true}
                className="p-2.5 rounded-full bg-active text-text-disabled cursor-not-allowed"
                aria-label="发送消息（需要输入内容）"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 隐藏的图片文件输入 */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={(e) => onImageSelect(e, maxImages, maxFileSize)}
        className="hidden"
        aria-label="选择图片文件"
        title="选择图片文件"
      />

      {/* 隐藏的 PDF 文件输入 */}
      <input
        ref={pdfFileInputRef}
        type="file"
        accept=".pdf,application/pdf"
        onChange={(e) => onFileSelect(e, maxPDFSize)}
        className="hidden"
        aria-label="选择 PDF 文件"
        title="选择 PDF 文件"
      />

      {/* workspace 独立上传已移除 — 统一走工作区面板 */}
    </div>
  );
}
