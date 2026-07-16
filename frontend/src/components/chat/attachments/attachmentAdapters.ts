import type { UploadedFile } from '../../../hooks/useFileUpload';
import type { UploadedImage } from '../../../hooks/useImageUpload';
import type { WorkspaceFile } from '../../../services/workspace';
import { categorize } from '../../../utils/fileCategory';
import { pickOriginalImageUrl, toDisplayThumbnailUrl, toOriginalImageUrl } from '../../../utils/imageUrlRules';
import type { ChatAttachment, ChatFileAttachment, ChatImageAttachment } from './ChatAttachment.types';

function toStatus(isUploading: boolean, error: string | null): 'uploading' | 'ready' | 'error' {
  if (error) return 'error';
  return isUploading ? 'uploading' : 'ready';
}

export function fromUploadedImage(image: UploadedImage): ChatImageAttachment {
  const originalUrl = image.url
    ? pickOriginalImageUrl(image.original_url, image.download_url, image.preview_url, image.url)
    : null;
  return {
    id: `image:${image.id}`,
    kind: 'image',
    source: image.isQuoted ? 'quote' : 'upload',
    sourceId: image.id,
    status: toStatus(image.isUploading, image.error),
    name: image.name || image.file.name,
    previewUrl: toDisplayThumbnailUrl(image.thumbnail_url, image.preview),
    originalUrl,
    thumbnailUrl: image.thumbnail_url,
    workspacePath: image.workspace_path,
    mimeType: image.mime_type || image.file.type || undefined,
    size: image.size ?? image.file.size,
    error: image.error || undefined,
  };
}

export function fromUploadedFile(file: UploadedFile): ChatFileAttachment {
  return {
    id: `file:${file.id}`,
    kind: 'file',
    source: 'upload',
    sourceId: file.id,
    status: toStatus(file.isUploading, file.error),
    name: file.name,
    url: file.url,
    workspacePath: file.workspace_path,
    mimeType: file.mime_type,
    size: file.size,
    error: file.error || undefined,
  };
}

export function fromWorkspaceFile(file: WorkspaceFile): ChatAttachment {
  const originalUrl = toOriginalImageUrl(file.cdn_url);
  if (categorize(file) === 'image') {
    return {
      id: `workspace:${file.workspace_path}`,
      kind: 'image',
      source: 'workspace',
      sourceId: file.workspace_path,
      status: originalUrl ? 'ready' : 'error',
      name: file.name,
      previewUrl: toDisplayThumbnailUrl(null, file.cdn_url),
      originalUrl: originalUrl || null,
      workspacePath: file.workspace_path,
      mimeType: file.mime_type || undefined,
      size: file.size,
      error: originalUrl ? undefined : '工作区图片缺少可用原图地址',
    };
  }
  return {
    id: `workspace:${file.workspace_path}`,
    kind: 'file',
    source: 'workspace',
    sourceId: file.workspace_path,
    status: 'ready',
    name: file.name,
    url: originalUrl,
    workspacePath: file.workspace_path,
    mimeType: file.mime_type || 'application/octet-stream',
    size: file.size,
  };
}
