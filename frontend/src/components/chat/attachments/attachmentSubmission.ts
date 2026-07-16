import type { ImageInputInfo } from '../../../services/messageSender';
import type {
  AttachmentSubmissionSnapshot,
  ChatAttachment,
  ChatImageAttachment,
  SubmissionFileInput,
} from './ChatAttachment.types';

function toImageInput(image: ChatImageAttachment): ImageInputInfo {
  const url = image.originalUrl as string;
  return {
    url,
    original_url: url,
    thumbnail_url: image.thumbnailUrl,
    name: image.name,
    workspace_path: image.workspacePath,
    mime_type: image.mimeType,
    size: image.size,
  };
}

function toFileInput(attachment: Extract<ChatAttachment, { kind: 'file' }>): SubmissionFileInput {
  return {
    url: attachment.url || '',
    name: attachment.name,
    mime_type: attachment.mimeType,
    size: attachment.size,
    workspace_path: attachment.workspacePath,
  };
}

/** 将界面附件模型转换为聊天、图片和视频接口共用的提交快照。 */
export function createAttachmentSubmissionSnapshot(
  attachments: ChatAttachment[],
): AttachmentSubmissionSnapshot {
  const readyImages = attachments.filter(
    (item): item is ChatImageAttachment => item.kind === 'image'
      && item.status === 'ready' && !!item.originalUrl,
  );
  return {
    attachments: [...attachments],
    imageInputs: readyImages.map(toImageInput),
    imageUrls: readyImages.map((image) => image.originalUrl as string),
    files: attachments
      .filter((item): item is Extract<ChatAttachment, { kind: 'file' }> => item.kind === 'file')
      .filter((item) => item.status === 'ready')
      .map(toFileInput),
    invalidImages: attachments.filter(
      (item): item is ChatImageAttachment => item.kind === 'image'
        && item.status !== 'uploading' && !item.originalUrl,
    ),
  };
}
