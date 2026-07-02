/**
 * 图片预览组件
 *
 * 显示上传的多张图片缩略图，支持：
 * - 删除功能
 * - 点击放大查看（集成 ImagePreviewModal）
 */

import { type UploadedImage } from '../../../hooks/useImageUpload';
import { usePreview } from '../../../preview/usePreview';
import PreviewHost from '../../../preview/PreviewHost';
import { fromBlobImage } from '../../../preview/toPreviewItem';
import { pickOriginalImageUrl, toDisplayThumbnailUrl } from '../../../utils/imageUrlRules';

interface ImagePreviewProps {
  images: UploadedImage[];
  onRemove: (imageId: string) => void;
}

export default function ImagePreview({ images, onRemove }: ImagePreviewProps) {
  const preview = usePreview();

  if (images.length === 0) return null;

  // 可预览的图片（排除上传中/错误）
  const previewableImages = images.filter((img) => !img.isUploading && !img.error && img.preview);

  // 把可预览图列表映射为 PreviewItem 数组（顺序与 previewableImages 一致）
  const previewItems = previewableImages.map((img) =>
    fromBlobImage({
      previewUrl: img.preview!,
      originalUrl: pickOriginalImageUrl(img.original_url, img.download_url, img.preview_url, img.url),
      thumbnailUrl: toDisplayThumbnailUrl(img.thumbnail_url, img.preview),
      filename: img.file.name.replace(/\.[^.]+$/, ''),
    }),
  );

  // 点击缩略图打开预览
  const handleImageClick = (image: UploadedImage) => {
    if (image.isUploading || image.error || !image.preview) return;
    const index = previewableImages.findIndex((img) => img.id === image.id);
    if (index >= 0) preview.open(previewItems, index);
  };

  // 删除当前预览图（透传给 PreviewHost.onDelete）
  // 删除后自动切换到下一张或上一张（与原逻辑等价）
  const handleDelete = () => {
    if (preview.state.kind !== 'open') return;
    const currentIdx = preview.state.index;
    const currentImage = previewableImages[currentIdx];
    if (!currentImage) return;
    const imageId = currentImage.id;
    const remainingCount = previewableImages.length - 1;
    if (remainingCount === 0) {
      preview.close();
    } else if (currentIdx >= remainingCount) {
      // 删的是最后一张 → 切到前一张
      preview.setIndex(currentIdx - 1);
    }
    // 其他情况：索引不变，自动显示下一张
    onRemove(imageId);
  };

  return (
    <>
      <div className="flex gap-2">
        {images.map((image) => (
          <div key={image.id} className="relative inline-block shrink-0">
            {image.preview ? (
              <img
                src={image.preview}
                alt={image.isQuoted ? '引用图片' : `预览 ${image.file.name}`}
                onClick={() => handleImageClick(image)}
                className={`h-12 w-12 rounded-lg object-cover transition-transform ${
                  image.isUploading ? 'opacity-50' : ''
                } ${image.error ? 'border-2 border-error' : ''} ${
                  image.isQuoted ? 'ring-2 ring-blue-400' : ''
                } ${
                  !image.isUploading && !image.error ? 'cursor-pointer hover:scale-105 hover:shadow-md' : ''
                }`}
              />
            ) : (
            <div className="h-12 w-12 rounded-lg bg-active flex items-center justify-center">
              <div className="w-4 h-4 border-2 border-text-disabled border-t-transparent rounded-full animate-spin"></div>
            </div>
          )}

          {/* 上传中指示器 */}
          {image.isUploading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
            </div>
          )}

          {/* 错误提示 */}
          {image.error && (
            <div className="absolute inset-0 flex items-center justify-center bg-error bg-opacity-75 rounded-lg">
              <svg className="w-5 h-5 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                  clipRule="evenodd"
                />
              </svg>
            </div>
          )}

          {/* 删除按钮 */}
          <button
            onClick={() => onRemove(image.id)}
            disabled={image.isUploading}
            className="absolute top-0 right-0 w-4 h-4 bg-black/60 text-white rounded-full flex items-center justify-center hover:bg-black/80 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
            title={image.error ? `删除失败的图片: ${image.error}` : '删除图片'}
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>

          {/* 引用图：左上角引号图标 */}
          {image.isQuoted && (
            <div className="absolute top-0.5 left-0.5 text-accent drop-shadow">
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <path d="M4.583 17.321C3.553 16.227 3 15 3 13.011c0-3.5 2.457-6.637 6.03-8.188l.893 1.378c-3.335 1.804-3.987 4.145-4.247 5.621.537-.278 1.24-.375 1.929-.311C9.591 11.69 11 13.168 11 15c0 1.933-1.567 3.5-3.5 3.5-1.172 0-2.204-.544-2.917-1.179zm10 0C13.553 16.227 13 15 13 13.011c0-3.5 2.457-6.637 6.03-8.188l.893 1.378c-3.335 1.804-3.987 4.145-4.247 5.621.537-.278 1.24-.375 1.929-.311C19.591 11.69 21 13.168 21 15c0 1.933-1.567 3.5-3.5 3.5-1.172 0-2.204-.544-2.917-1.179z" />
              </svg>
            </div>
          )}

          {/* 角标：引用图显示蓝色"引用"，上传图显示数字序号 */}
          <div className={`absolute bottom-0 left-0 text-white text-[10px] px-1 rounded-br-lg rounded-tl ${
            image.isQuoted ? 'bg-accent bg-opacity-85' : 'bg-surface-dark-card bg-opacity-75'
          }`}>
            {image.isQuoted ? '引用' : images.filter((img) => !img.isQuoted).indexOf(image) + 1}
          </div>
        </div>
      ))}
    </div>

      {/* 图片放大预览弹窗 — 统一走 PreviewHost（onDelete 透传保留删图能力）*/}
      <PreviewHost
        state={preview.state}
        onClose={preview.close}
        onIndexChange={preview.setIndex}
        onDelete={handleDelete}
      />
    </>
  );
}
