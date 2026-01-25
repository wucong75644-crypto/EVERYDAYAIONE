/**
 * 上传菜单组件
 */

import { type UnifiedModel } from '../../constants/models';

interface UploadMenuProps {
  visible: boolean;
  selectedModel: UnifiedModel;
  onImageUpload: () => void;
  onClose: () => void;
}

export default function UploadMenu({
  visible,
  selectedModel,
  onImageUpload,
  onClose,
}: UploadMenuProps) {
  if (!visible) return null;

  const supportsImageUpload =
    selectedModel.capabilities.imageEditing ||
    selectedModel.capabilities.imageToVideo ||
    selectedModel.capabilities.vqa ||
    selectedModel.capabilities.videoQA;

  const supportsDocumentUpload = selectedModel.type === 'chat';

  // 如果没有任何上传选项，不显示菜单
  if (!supportsImageUpload && !supportsDocumentUpload) {
    return null;
  }

  return (
    <div className="absolute bottom-full left-12 mb-2 bg-white rounded-lg shadow-lg border border-gray-200 overflow-hidden z-10 min-w-[200px]">
      {/* 上传图片 */}
      {supportsImageUpload && (
        <button
          onClick={() => {
            onImageUpload();
            onClose();
          }}
          className="w-full px-4 py-2 text-left hover:bg-gray-50 transition-colors flex items-center space-x-3"
        >
          <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
          <div>
            <div className="text-sm font-medium text-gray-900">上传图片</div>
            <div className="text-xs text-gray-500">支持 PNG, JPG, GIF</div>
          </div>
        </button>
      )}

      {/* 屏幕截图 */}
      {supportsImageUpload && (
        <button
          onClick={() => {
            onClose();
          }}
          disabled
          className="w-full px-4 py-2 text-left text-gray-400 cursor-not-allowed flex items-center space-x-3"
        >
          <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <div>
            <div className="text-sm font-medium">屏幕截图</div>
            <div className="text-xs">暂不支持</div>
          </div>
        </button>
      )}
    </div>
  );
}
