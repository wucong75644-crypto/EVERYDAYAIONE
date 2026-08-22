import type {
  ImageInputInfo,
  OrderedAttachmentInput,
} from '../../../services/messageSender';
import type {
  AttachmentSubmissionSnapshot,
  ChatAttachment,
  ChatImageAttachment,
  SubmissionFileInput,
} from './ChatAttachment.types';

export function toImageInput(image: ChatImageAttachment): ImageInputInfo {
  const url = image.originalUrl as string;
  return {
    url,
    original_url: url,
    thumbnail_url: image.thumbnailUrl,
    asset_id: image.assetId,
    name: image.name,
    workspace_path: image.workspacePath,
    mime_type: image.mimeType,
    size: image.size,
  };
}

export function toFileInput(attachment: Extract<ChatAttachment, { kind: 'file' }>): SubmissionFileInput {
  return {
    url: attachment.url || '',
    name: attachment.name,
    mime_type: attachment.mimeType,
    size: attachment.size,
    workspace_path: attachment.workspacePath,
  };
}

export function toOrderedAttachmentInputs(
  attachments: ChatAttachment[],
): OrderedAttachmentInput[] {
  const ordered: OrderedAttachmentInput[] = [];
  for (const attachment of attachments) {
    if (attachment.kind === 'image' && attachment.status === 'ready' && attachment.originalUrl) {
      ordered.push({ kind: 'image', image: toImageInput(attachment) });
      continue;
    }
    if (attachment.kind === 'file' && attachment.status === 'ready' && attachment.url) {
      const file = toFileInput(attachment);
      ordered.push({ kind: 'file', file });
    }
  }
  return ordered;
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
    files: attachments
      .filter((item): item is Extract<ChatAttachment, { kind: 'file' }> => item.kind === 'file')
      .filter((item) => item.status === 'ready')
      .map(toFileInput),
    orderedAttachments: toOrderedAttachmentInputs(attachments),
    invalidImages: attachments.filter(
      (item): item is ChatImageAttachment => item.kind === 'image'
        && item.status !== 'uploading' && !item.originalUrl,
    ),
  };
}
