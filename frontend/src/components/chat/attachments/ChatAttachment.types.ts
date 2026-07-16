import type { ImageInputInfo } from '../../../services/messageSender';

export type ChatAttachmentSource = 'upload' | 'quote' | 'workspace';
export type ChatAttachmentStatus = 'uploading' | 'ready' | 'error';

interface ChatAttachmentBase {
  id: string;
  source: ChatAttachmentSource;
  sourceId: string;
  status: ChatAttachmentStatus;
  name: string;
  workspacePath?: string;
  mimeType?: string;
  size?: number;
  error?: string;
}

export interface ChatImageAttachment extends ChatAttachmentBase {
  kind: 'image';
  previewUrl: string;
  originalUrl: string | null;
  thumbnailUrl?: string;
}

export interface ChatFileAttachment extends ChatAttachmentBase {
  kind: 'file';
  source: 'upload' | 'workspace';
  url: string | null;
  mimeType: string;
  size: number;
}

export type ChatAttachment = ChatImageAttachment | ChatFileAttachment;

export interface AttachmentConstraints {
  maxImages?: number;
  maxImageSizeMB?: number;
  maxFileSizeMB?: number;
}

export interface QuotedImageInput {
  url: string;
  thumbnailUrl?: string;
}

export interface SubmissionFileInput {
  url: string;
  name: string;
  mime_type: string;
  size: number;
  workspace_path?: string;
}

export interface AttachmentSubmissionSnapshot {
  attachments: ChatAttachment[];
  imageInputs: ImageInputInfo[];
  imageUrls: string[];
  files: SubmissionFileInput[];
  invalidImages: ChatImageAttachment[];
}

export interface AttachmentDraftTransaction {
  restore: () => void;
}
