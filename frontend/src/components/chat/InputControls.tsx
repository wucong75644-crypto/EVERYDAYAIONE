/**
 * 输入控制组件（重构版）
 *
 * 布局：图片预览 → 输入框 → 工具栏
 * 工具栏：左侧（模型/设置/深度思考） | 右侧（计费/上传/发送或语音）
 */

import { useState, useRef, useEffect } from 'react';
import { Send, Settings, Upload, Brain, Paperclip } from 'lucide-react';
import { type UnifiedModel } from '../../constants/models';
import { type AspectRatio, type ImageResolution, type ImageOutputFormat } from '../../services/image';
import { type VideoFrames, type VideoAspectRatio } from '../../services/video';
import ImagePreview from './ImagePreview';
import AudioPreview from './AudioPreview';
import ModelSelector from './ModelSelector';
import AdvancedSettingsMenu from './AdvancedSettingsMenu';
import UploadMenu from './UploadMenu';
import AudioRecorder from './AudioRecorder';
import { type UploadedImage } from '../../hooks/useImageUpload';
import { type RecordingState } from '../../hooks/useAudioRecording';
import { useDragDropUpload } from '../../hooks/useDragDropUpload';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../constants/animations';

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
  /** 是否需要上传图片（用于显示引导提示） */
  requiresImageUpload?: boolean;
}

export default function InputControls(props: InputControlsProps) {
  const {
    prompt, onPromptChange, onSubmit, onAudioSubmit, onKeyDown,
    isSubmitting, sendButtonDisabled, sendButtonTooltip,
    selectedModel, availableModels, modelSelectorLocked, modelSelectorLockTooltip, onSelectModel,
    estimatedCredits, creditsHighlight,
    aspectRatio, onAspectRatioChange, resolution, onResolutionChange, outputFormat, onOutputFormatChange,
    videoFrames, onVideoFramesChange, videoAspectRatio, onVideoAspectRatioChange,
    removeWatermark, onRemoveWatermarkChange, thinkingEffort, onThinkingEffortChange,
    deepThinkMode, onDeepThinkModeChange,
    onSaveSettings, onResetSettings,
    images, maxImages, maxFileSize, onRemoveImage, onImageSelect, onImageDrop, onImagePaste,
    recordingState, audioBlob, audioDuration, onStartRecording, onStopRecording, onClearRecording,
    requiresImageUpload = false,
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
  const hasContent = prompt.trim().length > 0 || images.length > 0;
  const canSubmit = !sendButtonDisabled && (hasContent || audioBlob);

  // 发送/语音按钮互斥显示
  const showSendButton = hasContent || audioBlob;
  const showVoiceButton = !showSendButton && selectedModel.capabilities.audioInput && onAudioSubmit;

  return (
    <div
      ref={dropZoneRef}
      className={`relative border rounded-2xl bg-white shadow-sm transition-all ${
        isDragging ? 'border-blue-500 border-2 bg-blue-50 shadow-lg' : 'border-gray-200 hover:shadow-md'
      }`}
    >
      {/* 拖拽提示 */}
      {isDragging && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-blue-50 bg-opacity-95 rounded-2xl pointer-events-none">
          <div className="text-center">
            <Upload className="w-12 h-12 text-blue-600 mx-auto mb-2" />
            <p className="text-lg font-medium text-blue-900">拖放图片到这里</p>
            <p className="text-sm text-blue-600">支持 PNG, JPG, GIF</p>
          </div>
        </div>
      )}

      <div className="p-3">
        {/* 图片预览区域（输入框顶部） */}
        {images.length > 0 && (
          <div className="mb-2">
            <ImagePreview images={images} onRemove={onRemoveImage} />
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
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={requiresImageUpload ? '该模型需要先上传图片才能生成哦～' : '发送消息...'}
          className="w-full resize-none border-none outline-none text-gray-900 placeholder-gray-400 text-base leading-6 min-h-[40px] max-h-[120px] overflow-y-auto"
          rows={1}
          disabled={isSubmitting}
        />

        {/* 底部工具栏 */}
        <div className="flex items-center justify-between mt-2">
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
                className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
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
                  videoFrames={videoFrames}
                  onVideoFramesChange={onVideoFramesChange}
                  videoAspectRatio={videoAspectRatio}
                  onVideoAspectRatioChange={onVideoAspectRatioChange}
                  removeWatermark={removeWatermark}
                  onRemoveWatermarkChange={onRemoveWatermarkChange}
                  thinkingEffort={thinkingEffort}
                  onThinkingEffortChange={onThinkingEffortChange}
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
                className={`flex items-center space-x-1 px-2 py-1.5 rounded-lg text-sm transition-colors ${
                  deepThinkMode
                    ? 'bg-purple-100 text-purple-700 hover:bg-purple-200'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
                title={deepThinkMode ? '深度思考已开启' : '开启深度思考'}
              >
                <Brain className="w-4 h-4" />
                <span className="hidden sm:inline">深度思考</span>
              </button>
            )}
          </div>

          {/* 右侧：计费提示、上传、发送/语音 */}
          <div className="flex items-center space-x-2">
            {/* 计费提示（无边框背景，颜色渐变动画） */}
            <span
              className={`text-xs transition-colors duration-1000 ease-out ${
                creditsHighlight ? 'text-orange-600' : 'text-gray-400'
              }`}
            >
              {estimatedCredits}
            </span>

            {/* 上传按钮（始终显示，UploadMenu 内部根据模型能力显示不同选项） */}
            <div ref={uploadMenuRef} className="relative">
              <button
                onClick={() => setShowUploadMenu(!showUploadMenu)}
                className={`p-2 rounded-lg transition-all ${
                  uploadButtonGlowing
                    ? 'text-red-500 animate-upload-glow'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
                title={requiresImageUpload ? '点击上传图片' : '上传文件'}
              >
                <Paperclip className="w-4 h-4" />
              </button>
              <UploadMenu
                visible={showUploadMenu}
                closing={uploadMenuClosing}
                selectedModel={selectedModel}
                onImageUpload={() => fileInputRef.current?.click()}
                onClose={closeUploadMenu}
              />
            </div>

            {/* 发送按钮（有内容时显示） */}
            {showSendButton && (
              <button
                onClick={onSubmit}
                disabled={!canSubmit || isSubmitting}
                title={sendButtonTooltip}
                className={`p-2.5 rounded-full transition-all ${
                  canSubmit && !isSubmitting
                    ? 'bg-blue-600 text-white hover:bg-blue-700 shadow-md hover:shadow-lg'
                    : 'bg-gray-200 text-gray-400 cursor-not-allowed'
                }`}
              >
                <Send className="w-4 h-4" />
              </button>
            )}

            {/* 语音按钮（无内容时显示） */}
            {showVoiceButton && (
              <AudioRecorder
                isRecording={recordingState === 'recording'}
                recordingTime={audioDuration}
                audioURL={audioBlob ? URL.createObjectURL(audioBlob) : null}
                onStartRecording={onStartRecording}
                onStopRecording={onStopRecording}
                onClearAudio={onClearRecording}
                disabled={isSubmitting}
              />
            )}

            {/* 如果两个都不显示，显示一个占位的发送按钮 */}
            {!showSendButton && !showVoiceButton && (
              <button
                type="button"
                disabled={true}
                className="p-2.5 rounded-full bg-gray-200 text-gray-400 cursor-not-allowed"
                aria-label="发送消息（需要输入内容）"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 隐藏的文件输入 */}
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
    </div>
  );
}
