import type { KeyboardEvent } from 'react';
import type {
  AspectRatio,
  ImageCount,
  ImageOutputFormat,
  ImageResolution,
  UnifiedModel,
  VideoAspectRatio,
  VideoFrames,
} from '../../../constants/models';
import type { RecordingState } from '../../../hooks/useAudioRecording';
import type { UploadedFile } from '../../../hooks/useFileUpload';
import type { MentionResult } from '../../../hooks/useFileMention';
import type { UploadedImage } from '../../../hooks/useImageUpload';
import type { WorkspaceFile } from '../../../services/workspace';

export interface InputControlsProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: () => void;
  onAudioSubmit?: (audioBlob: Blob) => void;
  onKeyDown: (event: KeyboardEvent) => void;
  isSubmitting: boolean;
  sendButtonDisabled: boolean;
  sendButtonTooltip: string;
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
  onResolutionChange: (resolution: ImageResolution) => void;
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
  permissionMode?: 'auto' | 'ask' | 'plan';
  onPermissionModeChange?: (mode: 'auto' | 'ask' | 'plan') => void;
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
  isUploading: boolean;
  onRemoveImage: (imageId: string) => void;
  files: UploadedFile[];
  onRemoveFile: (fileId: string) => void;
  workspaceFiles?: WorkspaceFile[];
  onRemoveWorkspaceFile?: (workspacePath: string) => void;
  onOpenWorkspace?: () => void;
  onUnifiedFiles?: (files: File[]) => void;
  workspaceOpen?: boolean;
  requiresImageUpload?: boolean;
  sendError?: string | null;
  hasQuotedImage?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
  effectiveModelType?: 'chat' | 'image' | 'video';
  smartSubMode?: string;
  onSmartSubModeChange?: (mode: string) => void;
  onEnhancePrompt?: () => void;
  isEnhancing?: boolean;
  mentionDropdownVisible?: boolean;
  mentionResults?: MentionResult[];
  mentionActiveIndex?: number;
  mentionLoading?: boolean;
  onMentionSelect?: (file: MentionResult) => void;
  onMentionHover?: (index: number) => void;
  onMentionInputChange?: (value: string, cursorPos: number) => void;
}
