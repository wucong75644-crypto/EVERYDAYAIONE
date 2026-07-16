/**
 * 输入控制组件（重构版）
 *
 * 布局：图片预览 → 输入框 → 工具栏
 * 工具栏：左侧（模型/设置/深度思考） | 右侧（计费/上传/发送或语音）
 */

import { useState, useRef, useEffect } from 'react';
import { m } from 'framer-motion';
import { Send, Square, Settings, Upload, Brain, Paperclip, FolderOpen, ChevronUp, Zap, ShieldCheck, ListChecks } from 'lucide-react';
import { Popover, PopoverClose } from '../../primitives/Popover';
import { cn } from '../../../utils/cn';
import { SOFT_SPRING } from '../../../utils/motion';
import ImagePreview from '../media/ImagePreview';
import FilePreview from '../media/FilePreview';
import AudioPreview from '../media/AudioPreview';
import ModelSelector from './ModelSelector';
import AdvancedSettingsMenu from './AdvancedSettingsMenu';
import UploadMenu from './UploadMenu';
import AudioRecorder from './AudioRecorder';
import FileMentionDropdown from './FileMentionDropdown';
import WorkspaceAttachmentPreview from './WorkspaceAttachmentPreview';
import type { InputControlsProps } from './InputControls.types';
import { useDragDropUpload } from '../../../hooks/useDragDropUpload';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../../constants/animations';

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
    deepThinkMode, onDeepThinkModeChange, permissionMode, onPermissionModeChange,
    temperature, onTemperatureChange, topP, onTopPChange, topK, onTopKChange,
    maxOutputTokens, onMaxOutputTokensChange,
    onSaveSettings, onResetSettings,
    images, onRemoveImage,
    files, onRemoveFile,
    workspaceFiles = [], onRemoveWorkspaceFile, onOpenWorkspace, onUnifiedFiles, workspaceOpen = false,
    recordingState, audioBlob, audioDuration, onStartRecording, onStopRecording, onClearRecording,
    requiresImageUpload = false, sendError, hasQuotedImage = false,
    isStreaming = false, onStop,
    effectiveModelType = selectedModel.type, smartSubMode, onSmartSubModeChange,
    mentionDropdownVisible = false, mentionResults = [], mentionActiveIndex = 0,
    mentionLoading = false, onMentionSelect, onMentionHover, onMentionInputChange,
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
  const uploadMenuRef = useRef<HTMLDivElement>(null);
  const advancedMenuRef = useRef<HTMLDivElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  // 拖拽/粘贴统一走 onUnifiedFiles（图片走 useImageUpload，其他走 useFileUpload；
  // 与「上传文件」菜单完全对称）
  const { isDragging } = useDragDropUpload({
    dropZoneRef,
    textareaRef,
    onFiles: (files) => onUnifiedFiles?.(files),
  });

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
        !sendError && !isDragging && permissionMode === 'plan' && 'border-success/30 hover:shadow-md',
        !sendError && !isDragging && permissionMode === 'ask' && 'border-warning/30 hover:shadow-md',
        !sendError && !isDragging && permissionMode !== 'plan' && permissionMode !== 'ask' && 'border-border-default hover:shadow-md',
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

      {/* @ 文件提及下拉面板（输入框上方） */}
      {mentionDropdownVisible && onMentionSelect && onMentionHover && (
        <FileMentionDropdown
          results={mentionResults}
          activeIndex={mentionActiveIndex}
          loading={mentionLoading}
          onSelect={onMentionSelect}
          onHover={onMentionHover}
        />
      )}

      <div className="px-3 pt-2 pb-2">
        {/* 附件横排区域：图片 + PDF + 工作区文件 */}
        {(images.length > 0 || files.length > 0 || workspaceFiles.length > 0) && (
          <div className="mb-2 flex items-end gap-2 overflow-x-auto scrollbar-hide p-1">
            {/* 图片 */}
            {images.length > 0 && (
              <div className="shrink-0">
                <ImagePreview images={images} onRemove={onRemoveImage} />
              </div>
            )}
            {/* PDF */}
            {files.length > 0 && (
              <div className="shrink-0">
                <FilePreview files={files} onRemove={onRemoveFile} />
              </div>
            )}
            <WorkspaceAttachmentPreview files={workspaceFiles} onRemove={onRemoveWorkspaceFile} />
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
        <div className="flex items-start gap-2">
          <textarea
            ref={textareaRef}
            name="chat-input"
            value={prompt}
            onChange={(e) => {
              onPromptChange(e.target.value);
              onMentionInputChange?.(e.target.value, e.target.selectionStart ?? e.target.value.length);
            }}
            onKeyDown={onKeyDown}
            placeholder={hasQuotedImage ? '描述你想要的修改...' : requiresImageUpload ? '该模型需要先上传图片才能生成哦～' : smartSubMode === 'image-ecom' ? '描述你的产品和需求，如"221色拼豆收纳盒 淘宝5张主图"' : '发送消息...'}
            className="flex-1 resize-none border-none outline-none bg-transparent text-text-primary placeholder:text-text-disabled text-base leading-6 pt-2 pb-1 min-h-[44px] max-h-[120px] overflow-y-auto"
            rows={1}
            disabled={isSubmitting}
          />
        </div>

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
              smartSubMode={smartSubMode}
              onSmartSubModeChange={onSmartSubModeChange}
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
                  effectiveModelType={effectiveModelType}
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
            {/* 模式选择器（chat 模式）或 计费提示（图片/视频模式） */}
            {effectiveModelType === 'chat' && onPermissionModeChange ? (
              <Popover
                side="top"
                align="end"
                sideOffset={12}
                maxWidth={180}
                className="!p-1"
                trigger={
                  <button
                    className={cn(
                      'flex items-center gap-1 text-xs px-2 py-0.5 rounded-full transition-all duration-200',
                      'border',
                      permissionMode === 'plan'
                        ? 'text-success border-success/30 bg-success/5'
                        : permissionMode === 'ask'
                          ? 'text-warning border-warning/30 bg-warning/5'
                          : 'text-text-disabled border-transparent hover:text-text-secondary',
                    )}
                  >
                    {permissionMode === 'plan' ? '计划模式' : permissionMode === 'ask' ? '确认模式' : '自动模式'}
                    <ChevronUp className="w-3 h-3" />
                  </button>
                }
              >
                {([
                  { value: 'auto', label: '自动模式', desc: '全自动执行', icon: Zap, color: 'text-text-secondary' },
                  { value: 'ask', label: '确认模式', desc: '危险操作需确认', icon: ShieldCheck, color: 'text-warning' },
                  { value: 'plan', label: '计划模式', desc: '先规划再执行', icon: ListChecks, color: 'text-success' },
                ] as const).map(({ value, label, desc, icon: Icon, color }) => (
                  <PopoverClose key={value} asChild>
                    <button
                      onClick={() => onPermissionModeChange(value)}
                      className={cn(
                        'w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-left transition-colors',
                        'hover:bg-surface-hover',
                        permissionMode === value ? 'bg-surface-hover' : '',
                      )}
                    >
                      <Icon className={cn('w-3.5 h-3.5 flex-shrink-0', color)} />
                      <div className="min-w-0">
                        <div className={cn('text-xs font-medium', permissionMode === value ? color : 'text-text-primary')}>
                          {label}
                        </div>
                        <div className="text-[10px] text-text-disabled leading-tight">{desc}</div>
                      </div>
                    </button>
                  </PopoverClose>
                ))}
              </Popover>
            ) : (
              <span
                className={cn(
                  'text-xs transition-colors duration-1000 ease-out',
                  creditsHighlight ? 'text-warning' : 'text-text-disabled',
                )}
              >
                {estimatedCredits}
              </span>
            )}

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
                onFilesSelected={(files) => onUnifiedFiles?.(files)}
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

      {/* 文件选择 input 已迁入 UploadMenu（统一上传入口）；
          拖拽/粘贴由 useDragDropUpload 统一接管，按 mime 分流到 useImageUpload/useFileUpload。 */}
    </div>
  );
}
