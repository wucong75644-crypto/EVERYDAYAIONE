/**
 * FallbackAdapter — 不支持预览的类型兜底
 *
 * 命中规则：always match（priority=0，所有其他 adapter 没命中时才会到这里）。
 *
 * 行为对齐行业（Google Drive / 飞书 / Dropbox）：
 *   弹窗显示「该格式暂不支持预览」+ 大下载按钮，让用户明确知道不支持原因，
 *   而非静默下载（避免「我点了它却没反应/它怎么自己下载了」的困惑）。
 */

import { Download } from 'lucide-react';
import toast from 'react-hot-toast';
import PreviewFrame from '../PreviewFrame';
import { downloadFile } from '../../utils/downloadFile';
import { getFileIcon } from '../../utils/fileUtils';
import { resolvePreviewUrl } from '../fetchPreview';
import type { PreviewAdapter, PreviewCommonProps } from '../types';

function FallbackAdapterComponent({ item, onClose }: PreviewCommonProps) {
  const handleDownload = async () => {
    const url = resolvePreviewUrl(item);
    if (!url) {
      toast.error('下载失败：无可用 URL');
      return;
    }
    try {
      await downloadFile(url, item.filename);
    } catch {
      toast.error('下载失败，请重试');
    }
  };

  return (
    <PreviewFrame item={item} onClose={onClose}>
      <div className="flex flex-col items-center justify-center min-h-full px-6 text-center text-white">
        <div className="text-7xl mb-6" aria-hidden>
          {getFileIcon(item.filename)}
        </div>
        <div className="text-lg font-medium mb-2">该格式暂不支持预览</div>
        <div className="text-sm text-gray-300 mb-6 max-w-md">
          浏览器无法直接打开该文件类型，请下载到本地后查看。
        </div>
        <button
          type="button"
          onClick={handleDownload}
          className="flex items-center gap-2 px-6 py-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-base font-medium transition-colors"
        >
          <Download className="w-5 h-5" />
          <span>点击下载</span>
        </button>
      </div>
    </PreviewFrame>
  );
}

export const fallbackAdapter: PreviewAdapter = {
  id: 'fallback',
  label: '不支持的格式',
  priority: 0,
  match: () => true, // always match — registry 兜底
  Component: FallbackAdapterComponent,
  supportsNavigation: false,
};
