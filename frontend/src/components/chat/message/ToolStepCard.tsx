/**
 * 工具调用步骤折叠卡片
 *
 * 对标 Vercel AI SDK 的 <Tool> 组件。
 * 展示工具调用的名称、状态（running/completed/error）、耗时，
 * 折叠区可展开查看 summary / code / output。
 */

import { useState, memo } from 'react';
import { getToolCallText } from '../../../constants/placeholder';

interface ToolStepCardProps {
  toolName: string;
  toolCallId: string;
  status: 'running' | 'completed' | 'error';
  summary?: string;
  code?: string;
  output?: string;
  elapsedMs?: number;
}

/** 格式化耗时 */
function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = (ms / 1000).toFixed(1);
  return `${s}s`;
}

/** 获取工具显示名（去掉"正在"前缀，保留动作） */
function getToolLabel(toolName: string): string {
  const raw = getToolCallText(toolName);
  // "正在查询订单信息" → "查询订单信息"
  return raw.replace(/^正在/, '');
}

const isCodeTool = (name: string) => name === 'code_execute';

export default memo(function ToolStepCard({
  toolName,
  toolCallId,
  status,
  summary,
  code,
  output,
  elapsedMs,
}: ToolStepCardProps) {
  const [expanded, setExpanded] = useState(false);
  const label = getToolLabel(toolName);
  const hasContent = !!(summary || code || output);
  const canExpand = hasContent && status !== 'running';

  return (
    <div
      key={toolCallId}
      className="my-1.5 rounded-lg border border-border-default/60 bg-bg-subtle/40 overflow-hidden text-xs"
    >
      {/* Header — 始终可见 */}
      <button
        type="button"
        onClick={() => canExpand && setExpanded((p) => !p)}
        className={`flex w-full items-center gap-2 px-3 py-2 text-left ${
          canExpand ? 'cursor-pointer hover:bg-bg-subtle/80' : 'cursor-default'
        }`}
      >
        {/* 展开箭头（仅可展开时显示） */}
        {canExpand ? (
          <svg
            className={`w-3 h-3 shrink-0 text-text-tertiary transition-transform duration-200 ${
              expanded ? 'rotate-90' : ''
            }`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        ) : (
          <span className="w-3 shrink-0" />
        )}

        {/* 工具图标 */}
        <span className="shrink-0">
          {isCodeTool(toolName) ? '💻' : '🔧'}
        </span>

        {/* 工具名 */}
        <span className="font-medium text-text-secondary truncate flex-1">
          {label}
        </span>

        {/* 状态标签 */}
        {status === 'running' && (
          <span className="flex items-center gap-1 text-text-tertiary shrink-0">
            <span className="thinking-dots" style={{ fontSize: '8px' }}>
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span className="thinking-dot" />
            </span>
            <span>执行中</span>
          </span>
        )}
        {status === 'completed' && (
          <span className="flex items-center gap-1 text-green-600 dark:text-green-400 shrink-0">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
            {elapsedMs != null && <span className="text-text-disabled">{formatElapsed(elapsedMs)}</span>}
          </span>
        )}
        {status === 'error' && (
          <span className="flex items-center gap-1 text-red-500 shrink-0">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
            </svg>
            <span>失败</span>
            {elapsedMs != null && <span className="text-text-disabled ml-0.5">{formatElapsed(elapsedMs)}</span>}
          </span>
        )}
      </button>

      {/* 折叠内容区 */}
      {expanded && hasContent && (
        <div className="border-t border-border-default/40 px-3 py-2 space-y-2">
          {/* code_execute: 代码块 */}
          {code && (
            <div>
              <div className="text-[10px] font-medium text-text-tertiary mb-1 uppercase tracking-wider">代码</div>
              <pre className="rounded-md bg-[var(--color-bg-primary)] border border-border-default/40 p-2 overflow-x-auto text-[11px] leading-relaxed text-text-secondary">
                <code>{code}</code>
              </pre>
            </div>
          )}

          {/* code_execute: 输出块 */}
          {output && (
            <div>
              <div className="text-[10px] font-medium text-text-tertiary mb-1 uppercase tracking-wider">输出</div>
              <pre className="rounded-md bg-neutral-900 dark:bg-neutral-950 text-neutral-200 p-2 overflow-x-auto text-[11px] leading-relaxed">
                {output}
              </pre>
            </div>
          )}

          {/* 通用工具: summary */}
          {summary && !code && !output && (
            <div className="text-text-secondary text-xs leading-relaxed whitespace-pre-wrap">
              {summary}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
