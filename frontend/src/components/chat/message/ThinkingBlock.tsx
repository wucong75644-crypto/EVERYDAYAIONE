/**
 * 思考过程折叠展开组件
 *
 * 显示 AI 模型的推理过程（reasoning_content），默认折叠。
 * 支持流式输出时显示思考中动画，完成后显示思考时长。
 */

import { useState, useCallback, useMemo, useEffect, useRef, memo } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import { SOFT_SPRING } from '../../../utils/motion';

interface ThinkingBlockProps {
  /** 思考内容文本 */
  content: string;
  /** 是否正在流式思考中 */
  isThinking?: boolean;
  /** 思考开始时间戳（用于计算思考时长，前端 fallback） */
  thinkingStartTime?: number;
  /** 后端计算的精确耗时（毫秒），优先于 thinkingStartTime */
  durationMs?: number;
}

/** 格式化思考时长 */
function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return remaining > 0 ? `${minutes}分${remaining}秒` : `${minutes}分钟`;
}

export default memo(function ThinkingBlock({
  content,
  isThinking = false,
  thinkingStartTime,
  durationMs,
}: ThinkingBlockProps) {
  // 初始值直接对齐 props：流式挂载 = 展开，DB 加载 = 折叠，无多余渲染
  const [expanded, setExpanded] = useState(isThinking);
  const prevThinkingRef = useRef(isThinking);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  // 检测 isThinking 的 true→false 转换，延迟自动折叠（对标 Vercel Reasoning 组件）
  useEffect(() => {
    const wasThinking = prevThinkingRef.current;
    prevThinkingRef.current = isThinking;

    if (isThinking) {
      setExpanded(true);
    } else if (wasThinking) {
      // true → false：thinking 完成，延迟 1 秒折叠
      const timer = setTimeout(() => setExpanded(false), 1000);
      return () => clearTimeout(timer);
    }
  }, [isThinking]);

  // 计算思考时长：优先后端 durationMs，fallback 前端计算
  const durationText = useMemo(() => {
    if (isThinking) return '';
    if (durationMs != null) return `用时 ${formatDuration(durationMs)}`;
    if (thinkingStartTime) return `用时 ${formatDuration(Date.now() - thinkingStartTime)}`;
    return '';
  }, [isThinking, durationMs, thinkingStartTime]);

  // 无内容时不渲染
  if (!content && !isThinking) return null;

  return (
    <div className="mb-2">
      {/* 折叠/展开触发器 */}
      <button
        onClick={toggleExpanded}
        className="flex items-center gap-1.5 text-xs text-text-tertiary hover:text-text-secondary transition-base py-1 group"
      >
        {/* 展开/折叠图标 */}
        <svg
          className={`w-3 h-3 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>

        {/* 思考中动画 / 思考完成标签 */}
        {isThinking ? (
          <span className="flex items-center gap-1">
            <span className="thinking-sparkle">thinking</span>
            <span className="thinking-dots">
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span className="thinking-dot" />
            </span>
          </span>
        ) : (
          <span>
            Thought for
            {durationText && (
              <span className="ml-1 text-text-disabled">{durationText}</span>
            )}
          </span>
        )}
      </button>

      {/* 展开的思考内容 — V3：framer spring 展开动画（替代 CSS max-height 跳变） */}
      <AnimatePresence initial={false}>
        {expanded && content && (
          <m.div
            key="thinking-content"
            className="thinking-content mt-1 ml-4 pl-3 border-l-2 border-border-default overflow-hidden"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={SOFT_SPRING}
          >
            <div className="text-sm text-text-tertiary leading-relaxed whitespace-pre-wrap">
              {content.trimStart()}
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
});
