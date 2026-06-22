/**
 * ImageAdapter — 图片预览适配器
 *
 * 薄包装现有 `ImagePreviewModal.tsx`（471 行复杂交互组件，零改动）。
 * 把 PreviewCommonProps（统一接口）映射到 ImagePreviewModal 的具体 props。
 *
 * 所有 471 行的交互能力（缩放/拖拽/双击/滚轮/上下张/缩略图栏/删除/动画/键盘）
 * 100% 保留，因为底层 Modal 完全没动。
 */

import ImagePreviewModal from '../../components/chat/media/ImagePreviewModal';
import { IMAGE_EXTS } from '../registry';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';

function ImageAdapterComponent({
  item,
  siblings,
  index,
  onClose,
  onNavigate,
  onDelete,
}: PreviewCommonProps) {
  // 所有兄弟图片的 URL 列表（用于底部缩略图栏）
  const allImages = siblings.map((s) => s.url || '');

  return (
    <ImagePreviewModal
      imageUrl={item.url || null}
      filename={item.filename}
      onClose={onClose}
      onPrev={() => onNavigate(index - 1)}
      onNext={() => onNavigate(index + 1)}
      hasPrev={index > 0}
      hasNext={index < siblings.length - 1}
      allImages={allImages}
      currentIndex={index}
      onSelectImage={onNavigate}
      onDelete={onDelete}
    />
  );
}

function matchImage(item: PreviewItem): boolean {
  const ext = extOf(item.filename);
  if (IMAGE_EXTS.has(ext)) return true;
  if (item.mimeType?.startsWith('image/')) return true;
  return false;
}

export const imageAdapter: PreviewAdapter = {
  id: 'image',
  label: '图片',
  priority: 100,
  match: matchImage,
  Component: ImageAdapterComponent,
  supportsNavigation: true,
};
