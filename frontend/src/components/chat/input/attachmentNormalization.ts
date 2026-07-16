import type { ImageInputInfo } from '../../../services/messageSender';
import type { WorkspaceFile } from '../../../services/workspace';
import { categorize } from '../../../utils/fileCategory';
import { toOriginalImageUrl } from '../../../utils/imageUrlRules';

export interface SubmissionFileInput {
  url: string;
  name: string;
  mime_type: string;
  size: number;
  workspace_path?: string;
}

interface NormalizeSubmissionAttachmentsInput {
  uploadedImageUrls: string[];
  uploadedImages: ImageInputInfo[];
  uploadedFiles: SubmissionFileInput[];
  workspaceFiles: WorkspaceFile[];
}

export interface NormalizedSubmissionAttachments {
  imageInputs: ImageInputInfo[];
  imageUrls: string[];
  files: SubmissionFileInput[];
  invalidWorkspaceImages: WorkspaceFile[];
}

export function hasValidWorkspaceImage(files: WorkspaceFile[]): boolean {
  return files.some(
    (file) => categorize(file) === 'image' && !!toOriginalImageUrl(file.cdn_url),
  );
}

/** 将上传、引用和工作区附件收口为统一提交输入。 */
export function normalizeSubmissionAttachments(
  input: NormalizeSubmissionAttachmentsInput,
): NormalizedSubmissionAttachments {
  const workspaceImageInputs: ImageInputInfo[] = [];
  const workspaceImageUrls: string[] = [];
  const workspaceRegularFiles: SubmissionFileInput[] = [];
  const invalidWorkspaceImages: WorkspaceFile[] = [];

  for (const file of input.workspaceFiles) {
    const originalUrl = toOriginalImageUrl(file.cdn_url);
    if (categorize(file) === 'image') {
      if (!originalUrl) {
        invalidWorkspaceImages.push(file);
        workspaceRegularFiles.push(toSubmissionFile(file, ''));
        continue;
      }
      workspaceImageUrls.push(originalUrl);
      workspaceImageInputs.push({
        url: originalUrl,
        original_url: originalUrl,
        name: file.name,
        workspace_path: file.workspace_path,
        mime_type: file.mime_type || undefined,
        size: file.size,
      });
      continue;
    }
    workspaceRegularFiles.push(toSubmissionFile(file, originalUrl));
  }

  const uploadedImageInputs = completeUploadedImageInputs(
    input.uploadedImages,
    input.uploadedImageUrls,
  );

  return {
    imageInputs: [...uploadedImageInputs, ...workspaceImageInputs],
    imageUrls: [...input.uploadedImageUrls.map(toOriginalImageUrl).filter(Boolean), ...workspaceImageUrls],
    files: [...input.uploadedFiles, ...workspaceRegularFiles],
    invalidWorkspaceImages,
  };
}

function completeUploadedImageInputs(
  uploadedImages: ImageInputInfo[],
  uploadedImageUrls: string[],
): ImageInputInfo[] {
  const knownUrls = new Set(uploadedImages.map((image) => toOriginalImageUrl(image.url)));
  const missingInputs = uploadedImageUrls
    .map(toOriginalImageUrl)
    .filter((url) => url && !knownUrls.has(url))
    .map((url) => ({ url, original_url: url }));
  return [...uploadedImages, ...missingInputs];
}

function toSubmissionFile(file: WorkspaceFile, url: string): SubmissionFileInput {
  return {
    url,
    name: file.name,
    mime_type: file.mime_type || 'application/octet-stream',
    size: file.size,
    workspace_path: file.workspace_path,
  };
}
