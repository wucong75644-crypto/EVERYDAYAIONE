/**
 * 上传菜单组件
 *
 * 改造（V2 - 设计系统重构）：
 * - 全 token 化（跟随主题）
 * - 4 处内联 SVG → lucide-react
 * - 提取 MENU_ITEM_CLASS 常量减少重复
 */

import { ImagePlus, Camera, FileText, Folder } from 'lucide-react';
import { type UnifiedModel } from '../../../constants/models';

interface UploadMenuProps {
  visible: boolean;
  closing?: boolean;
  selectedModel: UnifiedModel;
  onImageUpload: () => void;
  onFileUpload?: () => void;
  onWorkspaceUpload?: () => void;
  onClose: () => void;
}

/** 启用状态菜单项 */
const ITEM_ENABLED = 'hover:bg-hover text-text-primary';
/** 禁用状态菜单项 */
const ITEM_DISABLED = 'text-text-disabled cursor-not-allowed';

export default function UploadMenu({
  visible,
  closing = false,
  selectedModel,
  onImageUpload,
  onFileUpload,
  onWorkspaceUpload,
  onClose,
}: UploadMenuProps) {
  if (!visible) return null;

  const supportsImageUpload =
    selectedModel.capabilities.imageEditing ||
    selectedModel.capabilities.imageToVideo ||
    selectedModel.capabilities.vqa ||
    selectedModel.capabilities.videoQA;

  const supportsDocumentUpload = !!selectedModel.capabilities.pdfInput;

  return (
    <div
      className={`absolute bottom-full right-0 mb-2 bg-surface-card rounded-lg shadow-lg border border-border-default overflow-hidden z-30 min-w-[200px] ${
        closing ? 'animate-popup-exit' : 'animate-popup-enter'
      }`}
    >
      {/* 上传图片 */}
      <button
        onClick={() => {
          if (supportsImageUpload) {
            onImageUpload();
            onClose();
          }
        }}
        disabled={!supportsImageUpload}
        className={`w-full px-4 py-2 text-left flex items-center space-x-3 transition-base ${
          supportsImageUpload ? ITEM_ENABLED : ITEM_DISABLED
        }`}
      >
        <ImagePlus className="w-5 h-5 text-text-tertiary" />
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
        className={`w-full px-4 py-2 text-left flex items-center space-x-3 ${ITEM_DISABLED}`}
      >
        <Camera className="w-5 h-5 text-text-tertiary" />
        <div>
          <div className="text-sm font-medium">屏幕截图</div>
          <div className="text-xs">暂不支持</div>
        </div>
      </button>

      {/* 上传文档 */}
      <button
        onClick={() => {
          if (supportsDocumentUpload && onFileUpload) {
            onFileUpload();
            onClose();
          }
        }}
        disabled={!supportsDocumentUpload || !onFileUpload}
        className={`w-full px-4 py-2 text-left flex items-center space-x-3 transition-base ${
          supportsDocumentUpload && onFileUpload ? ITEM_ENABLED : ITEM_DISABLED
        }`}
      >
        <FileText className="w-5 h-5 text-text-tertiary" />
        <div>
          <div className="text-sm font-medium">上传文档</div>
          <div className="text-xs">
            {supportsDocumentUpload ? '支持 PDF 文档' : '当前模型不支持'}
          </div>
        </div>
      </button>

      {/* 分隔线 */}
      <div className="border-t border-border-light my-1" />

      {/* 上传到工作区（供 AI 分析） */}
      <button
        onClick={() => {
          if (onWorkspaceUpload) {
            onWorkspaceUpload();
            onClose();
          }
        }}
        disabled={!onWorkspaceUpload}
        className={`w-full px-4 py-2 text-left flex items-center space-x-3 transition-base ${
          onWorkspaceUpload ? ITEM_ENABLED : ITEM_DISABLED
        }`}
      >
        <Folder className="w-5 h-5 text-text-tertiary" />
        <div>
          <div className="text-sm font-medium">上传到工作区</div>
          <div className="text-xs">CSV/Excel/文档等，AI 可读取分析</div>
        </div>
      </button>
    </div>
  );
}
