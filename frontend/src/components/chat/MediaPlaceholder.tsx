/**
 * 媒体生成占位符组件
 *
 * 显示图片/视频生成任务的进度状态
 * 支持：
 * - 任务类型图标（图片/视频）
 * - 运行时间计时
 * - 脉冲动画
 */

import { useState, useEffect, memo } from 'react';
import { Image, Video, Loader2 } from 'lucide-react';

type MediaType = 'image' | 'video';

interface MediaPlaceholderProps {
  /** 媒体类型 */
  type: MediaType;
  /** 任务开始时间（ISO字符串） */
  startTime: string;
  /** 自定义文本（可选） */
  text?: string;
}

/**
 * 格式化运行时间（秒 → mm:ss）
 */
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export default memo(function MediaPlaceholder({
  type,
  startTime,
  text,
}: MediaPlaceholderProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = new Date(startTime).getTime();
    const updateElapsed = () => {
      const now = Date.now();
      setElapsed(Math.floor((now - start) / 1000));
    };

    updateElapsed();
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [startTime]);

  const isImage = type === 'image';
  const Icon = isImage ? Image : Video;
  const displayText = text || (isImage ? '图片生成中...' : '视频生成中...');

  return (
    <div className="flex items-center gap-2 py-1">
      <Loader2 className="w-4 h-4 text-gray-400 dark:text-gray-500 animate-spin" />
      <Icon className="w-4 h-4 text-gray-400 dark:text-gray-500" />
      <span className="text-sm text-gray-700 dark:text-gray-300">{displayText}</span>
      <span className="text-xs text-gray-400 dark:text-gray-500">
        {formatDuration(elapsed)}
      </span>
    </div>
  );
});
