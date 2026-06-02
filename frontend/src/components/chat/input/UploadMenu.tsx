/**
 * 上传菜单组件（合并版）
 *
 * 历史演进：
 * - V1: 上传图片 / 屏幕截图(disabled) / 上传文档 三项独立
 * - V3: 加入「上传到工作区」入口（target_dir=根目录）
 * - V4（本版）：合并为单一「上传文件」入口
 *   - 内部统一 file input，accept 涵盖图片+文档+数据+文本
 *   - 上传后所有文件统一落工作区 上传/{YYYY-MM}/（后端双写已就绪）
 *   - 屏幕截图 disabled 项删除（暂未实现，不放占位）
 *   - 「上传到工作区」语义合并入此入口（独立工作区面板的上传按钮不变）
 */

import { useRef } from 'react';
import { Upload } from 'lucide-react';

interface UploadMenuProps {
  visible: boolean;
  closing?: boolean;
  /** 用户选好文件后的统一回调（InputArea 内部按 mime 分流到 useImageUpload / useFileUpload） */
  onFilesSelected: (files: File[]) => void;
  onClose: () => void;
}

/** 允许的扩展名 = 图片 + 后端 _WORKSPACE_ALLOWED_EXTENSIONS（剔除 svg） */
const ACCEPT_EXTS = [
  // 图片
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
  // 文档
  '.pdf', '.doc', '.docx', '.ppt', '.pptx',
  // 数据
  '.xls', '.xlsx', '.csv', '.tsv',
  // 文本/代码/配置
  '.txt', '.md', '.json', '.yaml', '.yml', '.xml', '.log',
  '.py', '.js', '.ts', '.html', '.css', '.sql',
  // 压缩
  '.zip',
].join(',');

export default function UploadMenu({
  visible,
  closing = false,
  onFilesSelected,
  onClose,
}: UploadMenuProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!visible) return null;

  return (
    <div
      className={`absolute bottom-full right-0 mb-2 bg-surface-card rounded-lg shadow-lg border border-border-default overflow-hidden z-30 min-w-[220px] ${
        closing ? 'animate-popup-exit' : 'animate-popup-enter'
      }`}
    >
      <button
        onClick={() => fileInputRef.current?.click()}
        className="w-full px-4 py-3 text-left flex items-center space-x-3 transition-base hover:bg-hover text-text-primary"
      >
        <Upload className="w-5 h-5 text-text-tertiary" />
        <div>
          <div className="text-sm font-medium">上传文件</div>
          <div className="text-xs text-text-tertiary">
            支持图片 / PDF / Excel / Word / CSV 等
          </div>
        </div>
      </button>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT_EXTS}
        onChange={(e) => {
          const files = e.target.files;
          if (files && files.length > 0) {
            onFilesSelected(Array.from(files));
            onClose();
          }
          e.target.value = '';
        }}
        className="hidden"
        aria-label="上传文件"
      />
    </div>
  );
}
