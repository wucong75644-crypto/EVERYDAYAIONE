/**
 * VideoAdapter — 视频预览适配器
 *
 * 薄包装现有 `VideoPreviewModal.tsx`（170 行，零改动）。
 * VideoPreviewModal 不支持缩略图栏 / 删除回调（视频场景不需要），
 * 仅传递上下张 + 文件名 + 关闭。
 */

import VideoPreviewModal from '../../components/chat/media/VideoPreviewModal';
import { VIDEO_EXTS } from '../registry';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';

function VideoAdapterComponent({
  item,
  siblings,
  index,
  onClose,
  onNavigate,
}: PreviewCommonProps) {
  return (
    <VideoPreviewModal
      videoUrl={item.url || null}
      filename={item.filename}
      onClose={onClose}
      onPrev={() => onNavigate(index - 1)}
      onNext={() => onNavigate(index + 1)}
      hasPrev={index > 0}
      hasNext={index < siblings.length - 1}
    />
  );
}

function matchVideo(item: PreviewItem): boolean {
  const ext = extOf(item.filename);
  if (VIDEO_EXTS.has(ext)) return true;
  if (item.mimeType?.startsWith('video/')) return true;
  return false;
}

export const videoAdapter: PreviewAdapter = {
  id: 'video',
  label: '视频',
  priority: 100,
  match: matchVideo,
  Component: VideoAdapterComponent,
  supportsNavigation: true,
};
