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

  // 文档上传功能预留（当前禁用）
  const _supportsDocumentUpload = selectedModel.type === 'chat';
  void _supportsDocumentUpload;

  return (
    <div
      className="absolute bottom-full right-0 mb-2 bg-white rounded-lg shadow-lg border border-gray-200 overflow-hidden z-10 min-w-[200px]"
      style={{
        animation: 'dropdown-enter 150ms cubic-bezier(0.32, 0.72, 0, 1)',
      }}
    >
      <style>{`
        @keyframes dropdown-enter {
          from {
            opacity: 0;
            transform: scale(0.96) translateY(4px);
          }
          to {
            opacity: 1;
            transform: scale(1) translateY(0);
          }
        }
      `}</style>
      {/* 上传图片 */}
      <button
        onClick={() => {
          if (supportsImageUpload) {
            onImageUpload();
            onClose();
          }
        }}
        disabled={!supportsImageUpload}
        className={`w-full px-4 py-2 text-left flex items-center space-x-3 transition-colors ${
          supportsImageUpload
            ? 'hover:bg-gray-50 text-gray-900'
            : 'text-gray-400 cursor-not-allowed'
        }`}
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
          <div className="text-sm font-medium">上传图片</div>
          <div className="text-xs">
            {supportsImageUpload ? '支持 PNG, JPG, GIF' : '当前模型不支持'}
          </div>
        </div>
      </button>

      {/* 屏幕截图 */}
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

      {/* 上传文档 */}
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
            d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
          />
        </svg>
        <div>
          <div className="text-sm font-medium">上传文档</div>
          <div className="text-xs">暂不支持</div>
        </div>
      </button>
    </div>
  );
}
