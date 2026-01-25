/**
 * 图片预览组件
 *
 * 显示上传的多张图片缩略图，提供删除功能
 */

import { type UploadedImage } from '../../hooks/useImageUpload';

interface ImagePreviewProps {
  images: UploadedImage[];
  onRemove: (imageId: string) => void;
}

export default function ImagePreview({ images, onRemove }: ImagePreviewProps) {
  if (images.length === 0) return null;

  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {images.map((image) => (
        <div key={image.id} className="relative inline-block">
          {image.preview ? (
            <img
              src={image.preview}
              alt={`预览 ${image.file.name}`}
              className={`h-14 w-14 rounded-lg object-cover ${
                image.isUploading ? 'opacity-50' : ''
              } ${image.error ? 'border-2 border-red-500' : ''}`}
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
  );
}
