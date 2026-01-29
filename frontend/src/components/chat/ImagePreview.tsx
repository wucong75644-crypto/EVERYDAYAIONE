/**
 * 图片预览组件
 *
 * 显示上传的多张图片缩略图，支持：
 * - 删除功能
 * - 点击放大查看（集成 ImagePreviewModal）
 */

import { useState } from 'react';
import { type UploadedImage } from '../../hooks/useImageUpload';
import ImagePreviewModal from './ImagePreviewModal';

interface ImagePreviewProps {
  images: UploadedImage[];
  onRemove: (imageId: string) => void;
}

export default function ImagePreview({ images, onRemove }: ImagePreviewProps) {
  // 当前放大预览的图片索引（-1 表示未预览）
  const [previewIndex, setPreviewIndex] = useState<number>(-1);

  if (images.length === 0) return null;

  // 获取可预览的图片列表（排除上传中和错误的）
  const previewableImages = images.filter((img) => !img.isUploading && !img.error && img.preview);

  // 当前预览的图片
  const currentPreviewImage = previewIndex >= 0 ? previewableImages[previewIndex] : null;

  // 点击缩略图放大
  const handleImageClick = (image: UploadedImage) => {
    // 上传中或有错误时不允许放大
    if (image.isUploading || image.error || !image.preview) return;
    const index = previewableImages.findIndex((img) => img.id === image.id);
    if (index >= 0) {
      setPreviewIndex(index);
    }
  };

  // 切换到上一张
  const handlePrev = () => {
    if (previewIndex > 0) {
      setPreviewIndex(previewIndex - 1);
    }
  };

  // 切换到下一张
  const handleNext = () => {
    if (previewIndex < previewableImages.length - 1) {
      setPreviewIndex(previewIndex + 1);
    }
  };

  // 删除当前预览的图片
  const handleDelete = () => {
    if (!currentPreviewImage) return;
    const imageId = currentPreviewImage.id;

    // 删除后自动切换到下一张或上一张
    if (previewableImages.length === 1) {
      // 只有一张图片，关闭预览
      setPreviewIndex(-1);
    } else if (previewIndex >= previewableImages.length - 1) {
      // 删除的是最后一张，切换到前一张
      setPreviewIndex(previewIndex - 1);
    }
    // 其他情况保持当前索引，会自动显示下一张

    onRemove(imageId);
  };

  return (
    <>
      <div className="mb-2 flex flex-wrap gap-2">
        {images.map((image) => (
          <div key={image.id} className="relative inline-block">
            {image.preview ? (
              <img
                src={image.preview}
                alt={`预览 ${image.file.name}`}
                onClick={() => handleImageClick(image)}
                className={`h-14 w-14 rounded-lg object-cover transition-transform ${
                  image.isUploading ? 'opacity-50' : ''
                } ${image.error ? 'border-2 border-red-500' : ''} ${
                  !image.isUploading && !image.error ? 'cursor-pointer hover:scale-105 hover:shadow-md' : ''
                }`}
              />
            ) : (
            <div className="h-14 w-14 rounded-lg bg-gray-200 flex items-center justify-center">
              <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin"></div>
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
            <div className="absolute inset-0 flex items-center justify-center bg-red-500 bg-opacity-75 rounded-lg">
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
            className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-gray-800 text-white rounded-full flex items-center justify-center hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
            title={image.error ? `删除失败的图片: ${image.error}` : '删除图片'}
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>

          {/* 图片序号 */}
          <div className="absolute bottom-0 left-0 bg-gray-800 bg-opacity-75 text-white text-[10px] px-1 rounded-br-lg rounded-tl">
            {images.indexOf(image) + 1}
          </div>
        </div>
      ))}
    </div>

      {/* 图片放大预览弹窗 */}
      {currentPreviewImage && (
        <ImagePreviewModal
          imageUrl={currentPreviewImage.preview}
          onClose={() => setPreviewIndex(-1)}
          filename={currentPreviewImage.file.name.replace(/\.[^.]+$/, '')}
          onDelete={handleDelete}
          onPrev={handlePrev}
          onNext={handleNext}
          hasPrev={previewIndex > 0}
          hasNext={previewIndex < previewableImages.length - 1}
          allImages={previewableImages.map((img) => img.preview)}
          currentIndex={previewIndex}
          onSelectImage={setPreviewIndex}
        />
      )}
    </>
  );
}
