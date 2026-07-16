import { useMemo } from 'react';
import PreviewHost from '../../../preview/PreviewHost';
import type { PreviewItem } from '../../../preview/types';
import { usePreview } from '../../../preview/usePreview';
import { getFileIcon } from '../../../utils/fileUtils';
import type { ChatAttachment, ChatImageAttachment } from './ChatAttachment.types';

interface ChatAttachmentPreviewProps {
  attachments: ChatAttachment[];
  onRemove: (id: string) => void;
}

function toPreviewItem(image: ChatImageAttachment): PreviewItem {
  return {
    url: image.originalUrl || image.previewUrl || undefined,
    thumbnailUrl: image.previewUrl || undefined,
    workspacePath: image.workspacePath,
    filename: image.name,
    mimeType: image.mimeType || 'image/*',
    size: image.size,
  };
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

export default function ChatAttachmentPreview({ attachments, onRemove }: ChatAttachmentPreviewProps) {
  const preview = usePreview();
  const previewableImages = useMemo(() => attachments.filter(
    (item): item is ChatImageAttachment => item.kind === 'image'
      && item.status === 'ready' && !!(item.originalUrl || item.previewUrl),
  ), [attachments]);
  const previewItems = useMemo(() => previewableImages.map(toPreviewItem), [previewableImages]);

  const openImage = (image: ChatImageAttachment) => {
    const index = previewableImages.findIndex((item) => item.id === image.id);
    if (index >= 0) preview.open(previewItems, index);
  };

  const removeFromPreview = () => {
    if (preview.state.kind !== 'open') return;
    const current = previewableImages[preview.state.index];
    if (!current) return;
    onRemove(current.id);
    preview.close();
  };

  if (attachments.length === 0) return null;
  return (
    <>
      <div className="flex items-end gap-2">
        {attachments.map((attachment) => attachment.kind === 'image' ? (
          <div key={attachment.id} className="relative inline-block shrink-0">
            {attachment.previewUrl ? (
              <img
                src={attachment.previewUrl}
                alt={attachment.name}
                onClick={() => openImage(attachment)}
                className={`h-12 w-12 rounded-lg object-cover transition-transform ${
                  attachment.status === 'uploading' ? 'opacity-50' : ''
                } ${attachment.status === 'error' ? 'border-2 border-error' : ''} ${
                  attachment.source === 'quote' ? 'ring-2 ring-blue-400' : ''
                } ${attachment.status === 'ready' ? 'cursor-pointer hover:scale-105 hover:shadow-md' : ''}`}
              />
            ) : (
              <div className="h-12 w-12 rounded-lg bg-active flex items-center justify-center text-error">!</div>
            )}
            {attachment.status === 'uploading' && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              </div>
            )}
            <button
              type="button"
              onClick={() => onRemove(attachment.id)}
              disabled={attachment.status === 'uploading'}
              className="absolute top-0 right-0 w-4 h-4 bg-black/60 text-white rounded-full flex items-center justify-center hover:bg-black/80 disabled:opacity-50"
              aria-label={`移除 ${attachment.name}`}
            >×</button>
            {attachment.source === 'quote' && (
              <div className="absolute bottom-0 left-0 bg-accent/85 text-white text-[10px] px-1 rounded-br-lg rounded-tl">引用</div>
            )}
          </div>
        ) : (
          <div key={attachment.id} className="relative flex items-center gap-2 rounded-lg border border-border-default bg-surface px-3 py-2 text-sm">
            <span className="shrink-0 text-base">{getFileIcon(attachment.name)}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate max-w-[160px] font-medium text-text-secondary">{attachment.name}</div>
              <div className="text-xs text-text-disabled">{attachment.error || formatFileSize(attachment.size)}</div>
            </div>
            <button type="button" onClick={() => onRemove(attachment.id)} disabled={attachment.status === 'uploading'} aria-label={`移除 ${attachment.name}`}>×</button>
          </div>
        ))}
      </div>
      <PreviewHost state={preview.state} onClose={preview.close} onIndexChange={preview.setIndex} onDelete={removeFromPreview} />
    </>
  );
}
