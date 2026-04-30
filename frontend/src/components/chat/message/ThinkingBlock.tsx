/**
 * 思考过程折叠展开组件
 *
 * 显示 AI 模型的推理过程（reasoning_content），默认折叠。
 * 支持流式输出时显示思考中动画，完成后显示思考时长。
 * 支持展示工具执行步骤（带可折叠代码块）。
 */

import { useState, useCallback, useMemo, memo } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import { SOFT_SPRING } from '../../../utils/motion';

export interface ToolStep {
  toolName: string;
  status: 'running' | 'completed' | 'error';
  summary?: string;
  code?: string;
  resultText?: string;
}

interface ThinkingBlockProps {
  /** 思考内容文本 */
  content: string;
  /** 是否正在流式思考中 */
  isThinking?: boolean;
  /** 思考开始时间戳（用于计算思考时长，前端 fallback） */
  thinkingStartTime?: number;
  /** 后端计算的精确耗时（毫秒），优先于 thinkingStartTime */
  durationMs?: number;
  /** 工具执行步骤（在 thinking 展开区域内渲染，代码默认折叠） */
  steps?: ToolStep[];
}

/** 格式化思考时长 */
function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return remaining > 0 ? `${minutes}分${remaining}秒` : `${minutes}分钟`;
}

const STATUS_ICON: Record<ToolStep['status'], string> = {
  running: '…',
  completed: '✓',
  error: '✗',
};

const STATUS_COLOR: Record<ToolStep['status'], string> = {
  running: 'text-text-tertiary',
  completed: 'text-green-500',
  error: 'text-red-500',
};

/** 单个工具步骤：标题行 + 可折叠代码/结果 */
const StepItem = memo(function StepItem({ step }: { step: ToolStep }) {
  const [codeExpanded, setCodeExpanded] = useState(false);
  const hasDetail = !!(step.code || step.resultText);

  return (
    <div className="mt-1.5">
      <button
        onClick={hasDetail ? () => setCodeExpanded(prev => !prev) : undefined}
        className={`flex items-center gap-1 text-xs ${hasDetail ? 'cursor-pointer hover:text-text-secondary' : 'cursor-default'} text-text-tertiary`}
      >
        <span className={STATUS_COLOR[step.status]}>{STATUS_ICON[step.status]}</span>
        <span className="font-medium">{step.toolName}</span>
        {step.summary && <span className="ml-1 opacity-70">{step.summary}</span>}
        {hasDetail && (
          <svg
            className={`w-2.5 h-2.5 ml-0.5 transition-transform duration-150 ${codeExpanded ? 'rotate-90' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        )}
      </button>
      <AnimatePresence initial={false}>
        {codeExpanded && hasDetail && (
          <m.div
            key="step-detail"
            className="overflow-hidden"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={SOFT_SPRING}
          >
            {step.code && (
              <pre className="mt-1 ml-3 p-2 text-xs bg-bg-tertiary rounded overflow-x-auto text-text-tertiary leading-relaxed">
                {step.code}
              </pre>
            )}
            {step.resultText && (
              <div className="mt-1 ml-3 p-2 text-xs bg-bg-tertiary rounded text-text-tertiary leading-relaxed whitespace-pre-wrap">
                {step.resultText}
              </div>
            )}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
});

export default memo(function ThinkingBlock({
  content,
  isThinking = false,
  thinkingStartTime,
  durationMs,
  steps,
}: ThinkingBlockProps) {
  // 默认折叠；用户手动展开后保持展开状态，不自动收起
  const [expanded, setExpanded] = useState(false);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  // 计算思考时长：优先后端 durationMs，fallback 前端计算
  const durationText = useMemo(() => {
    if (isThinking) return '';
    if (durationMs != null) return `用时 ${formatDuration(durationMs)}`;
    if (thinkingStartTime) return `用时 ${formatDuration(Date.now() - thinkingStartTime)}`;
    return '';
  }, [isThinking, durationMs, thinkingStartTime]);

  const hasSteps = steps && steps.length > 0;

  // 无内容时不渲染
  if (!content && !isThinking && !hasSteps) return null;

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

      {/* 展开的思考内容 — V3：framer spring 展开动画 */}
      <AnimatePresence initial={false}>
        {expanded && (content || hasSteps) && (
          <m.div
            key="thinking-content"
            className="thinking-content mt-1 ml-4 pl-3 border-l-2 border-border-default overflow-hidden"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={SOFT_SPRING}
          >
            {/* AI 原始思考文本 */}
            {content && (
              <div className="text-sm text-text-tertiary leading-relaxed whitespace-pre-wrap">
                {content.trimStart()}
              </div>
            )}
            {/* 工具执行步骤（代码默认折叠） */}
            {hasSteps && (
              <div className={content ? 'mt-2 pt-2 border-t border-border-default' : ''}>
                {steps.map((step, i) => (
                  <StepItem key={`${step.toolName}-${i}`} step={step} />
                ))}
              </div>
            )}
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
});
