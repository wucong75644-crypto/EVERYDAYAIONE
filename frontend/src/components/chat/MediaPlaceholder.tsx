/**
 * 媒体生成占位符组件
 *
 * 显示图片/视频生成任务的进度状态
 * 支持：
 * - 任务类型图标（图片/视频）
 * - 加载动画
 */

import { memo } from 'react';
import { Image, Video, Loader2 } from 'lucide-react';

type MediaType = 'image' | 'video';

interface MediaPlaceholderProps {
  /** 媒体类型 */
  type: MediaType;
  /** 自定义文本（可选） */
  text?: string;
}

export default memo(function MediaPlaceholder({
  type,
  text,
}: MediaPlaceholderProps) {
  const isImage = type === 'image';
  const Icon = isImage ? Image : Video;
  const displayText = text || (isImage ? '图片生成中...' : '视频生成中...');

  return (
    <div className="flex items-center gap-2 py-1">
      <Loader2 className="w-4 h-4 text-gray-400 dark:text-gray-500 animate-spin" />
      <Icon className="w-4 h-4 text-gray-400 dark:text-gray-500" />
      <span className="text-sm text-gray-700 dark:text-gray-300">{displayText}</span>
    </div>
  );
});
