/**
 * 统一的加载占位符组件（Claude 风格）
 *
 * - 有文字时：shimmer 渐变文字 + 三个脉冲小圆点
 * - 无文字时：仅三个脉冲小圆点
 * - 无卡片框，直接内联显示
 */

interface LoadingPlaceholderProps {
  /** 占位符文字（如 "正在查询订单..."），不传则只显示圆点 */
  text?: string;
  /** 自定义样式类名 */
  className?: string;
}

export default function LoadingPlaceholder({
  text,
  className = ''
}: LoadingPlaceholderProps) {
  return (
    <div className={`flex items-center gap-1.5 py-1 ${className}`}>
      {/* shimmer 文字（复用 ThinkingBlock 的 thinking-sparkle 样式） */}
      {text && (
        <span className="text-sm thinking-sparkle">{text}</span>
      )}
      {/* 三个脉冲小圆点（复用 thinking-dot 动画） */}
      <span className="thinking-dots" aria-hidden="true">
        <span className="thinking-dot" />
        <span className="thinking-dot" />
        <span className="thinking-dot" />
      </span>
    </div>
  );
}
