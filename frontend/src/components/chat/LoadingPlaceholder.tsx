/**
 * 统一的加载占位符组件
 *
 * 显示文字 + 三个跳动的小圆点
 * 适用于：聊天、图片、视频等所有类型的生成中状态
 */

import styles from './shared.module.css';

interface LoadingPlaceholderProps {
  /** 占位符文字（如："AI 正在思考"、"图片生成中"、"视频生成中"） */
  text: string;
  /** 自定义样式类名 */
  className?: string;
}

export default function LoadingPlaceholder({
  text,
  className = ''
}: LoadingPlaceholderProps) {
  return (
    <div className={`flex items-center space-x-2 text-gray-500 ${className}`}>
      <span className="text-sm">{text}</span>
      <div className="flex space-x-1">
        <span
          className={`w-2 h-2 bg-gray-400 rounded-full animate-bounce ${styles['bounce-dot-1']}`}
          aria-hidden="true"
        />
        <span
          className={`w-2 h-2 bg-gray-400 rounded-full animate-bounce ${styles['bounce-dot-2']}`}
          aria-hidden="true"
        />
        <span
          className={`w-2 h-2 bg-gray-400 rounded-full animate-bounce ${styles['bounce-dot-3']}`}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
