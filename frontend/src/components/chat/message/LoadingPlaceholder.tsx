/**
 * 统一的加载占位符组件（Claude 风格）
 *
 * - 有文字时：文字 + 三个脉冲小圆点
 * - 无文字时：仅三个脉冲小圆点
 * - 无卡片框，直接内联显示
 */

import './markdown.css';

interface LoadingPlaceholderProps {
  /** 占位符文字（如 "正在查询订单..."），不传则只显示圆点 */
  text?: string;
  /** 自定义样式类名 */
  className?: string;
  /** debug 标记：标识渲染来源 */
  source?: string;
}

export default function LoadingPlaceholder({
  text,
  className = '',
  source,
}: LoadingPlaceholderProps) {
  return (
    <div className={`flex items-center gap-1.5 py-1 ${className}`} data-source={source}>
      {text && (
        <span className="text-sm thinking-sparkle">{text}</span>
      )}
      <span className="thinking-dots" aria-hidden="true">
        <span className="thinking-dot" />
        <span className="thinking-dot" />
        <span className="thinking-dot" />
      </span>
    </div>
  );
}
