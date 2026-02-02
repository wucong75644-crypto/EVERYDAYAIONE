/**
 * 统一的媒体占位符组件
 *
 * 用于显示媒体生成中的灰色占位框 + 图标
 * 支持动态尺寸、淡入动画、深色模式
 */

import { Image as ImageIcon, Video as VideoIcon, Music as MusicIcon, Box as BoxIcon } from 'lucide-react';
import styles from './shared.module.css';

/** 媒体类型（可扩展） */
export type MediaType = 'image' | 'video' | 'audio' | '3d' | 'code';

/** 媒体占位符配置 */
interface MediaPlaceholderConfig {
  icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  label: string;
  iconSize?: string;
}

/** 媒体类型配置映射 */
const MEDIA_CONFIG: Record<MediaType, MediaPlaceholderConfig> = {
  image: {
    icon: ImageIcon,
    label: '正在生成图片',
    iconSize: 'w-10 h-10',
  },
  video: {
    icon: VideoIcon,
    label: '正在生成视频',
    iconSize: 'w-10 h-10',
  },
  audio: {
    icon: MusicIcon,
    label: '正在生成音频',
    iconSize: 'w-10 h-10',
  },
  '3d': {
    icon: BoxIcon,
    label: '正在生成 3D 模型',
    iconSize: 'w-10 h-10',
  },
  code: {
    icon: ImageIcon,
    label: '正在生成代码',
    iconSize: 'w-10 h-10',
  },
};

interface MediaPlaceholderProps {
  /** 媒体类型 */
  type: MediaType;
  /** 占位符宽度（px） */
  width: number;
  /** 占位符高度（px） */
  height: number;
  /** 自定义样式类名 */
  className?: string;
}

export default function MediaPlaceholder({
  type,
  width,
  height,
  className = '',
}: MediaPlaceholderProps) {
  const config = MEDIA_CONFIG[type];
  const Icon = config.icon;
  const iconSize = config.iconSize || 'w-10 h-10';

  return (
    <div
      className={`
        ${styles['dynamic-size']}
        rounded-xl
        bg-gray-100 dark:bg-gray-700
        flex items-center justify-center
        shadow-sm
        animate-fade-in
        ${className}
      `}
      style={{
        '--width': `${width}px`,
        '--height': `${height}px`,
      } as React.CSSProperties}
      role="status"
      aria-label={config.label}
    >
      <Icon
        className={`${iconSize} text-gray-300 dark:text-gray-500`}
        aria-hidden={true}
      />
    </div>
  );
}
