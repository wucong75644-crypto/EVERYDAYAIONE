/**
 * 工具调用步骤折叠卡片
 *
 * 对标 Vercel AI SDK 的 <Tool> 组件。
 * 展示工具调用的名称、状态（running/completed/error）、耗时，
 * 折叠区可展开查看 Input（调用参数）+ Result（返回结果）。
 */

import { useState, memo } from 'react';
import { getToolCallText } from '../../../constants/placeholder';

interface ToolStepCardProps {
  toolName: string;
  toolCallId: string;
  status: 'running' | 'completed' | 'error' | 'cancelled';
  code?: string;
  output?: string;
  input?: string;
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

/** 尝试格式化 JSON 字符串，失败则原样返回 */
function tryFormatJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

export default memo(function ToolStepCard({
  toolName,
  toolCallId,
  status,
  code,
  output,
  input,
  elapsedMs,
}: ToolStepCardProps) {
  const [expanded, setExpanded] = useState(false);
  const label = getToolLabel(toolName);
  const hasContent = !!(code || output || input);
  // running 不可展开（无内容）；cancelled 即使无 output 也可展开（用户看输入参数）
  const canExpand = hasContent && status !== 'running';
  const isCancelled = status === 'cancelled';

  return (
    <div
      key={toolCallId}
      className={`my-1.5 max-w-md rounded-lg border overflow-hidden text-xs ${
        isCancelled
          ? 'border-l-2 border-l-yellow-500 border-border-default/60 bg-bg-subtle/30'
          : 'border-border-default/60 bg-bg-subtle/40'
      }`}
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
        {status === 'cancelled' && (
          <span className="flex items-center gap-1 text-yellow-600 dark:text-yellow-500 shrink-0">
            <span className="text-[10px]">⏸</span>
            <span>已中断</span>
          </span>
        )}
      </button>

      {/* 折叠内容区：Input（调用参数）+ Result（返回结果），对齐 Claude 风格 */}
      {expanded && hasContent && (
        <div className="border-t border-border-default/40 px-3 py-2 space-y-2">
          {/* Input：调用参数（code_execute 显示代码，其他工具显示 JSON 参数） */}
          {code && (
            <div>
              <div className="text-[10px] font-medium text-text-tertiary mb-1 uppercase tracking-wider">Input</div>
              <pre className="rounded-md bg-[var(--color-bg-primary)] border border-border-default/40 p-2 overflow-x-auto text-[11px] leading-relaxed text-text-secondary max-h-60 overflow-y-auto">
                <code>{code}</code>
              </pre>
            </div>
          )}
          {input && !code && (
            <div>
              <div className="text-[10px] font-medium text-text-tertiary mb-1 uppercase tracking-wider">Input</div>
              <pre className="rounded-md bg-[var(--color-bg-primary)] border border-border-default/40 p-2 overflow-x-auto text-[11px] leading-relaxed text-text-secondary max-h-60 overflow-y-auto">
                <code>{tryFormatJson(input)}</code>
              </pre>
            </div>
          )}

          {/* Result：返回结果 */}
          {output && (
            <div>
              <div className="text-[10px] font-medium text-text-tertiary mb-1 uppercase tracking-wider">Result</div>
              <pre className={`rounded-md p-2 overflow-x-auto text-[11px] leading-relaxed max-h-60 overflow-y-auto whitespace-pre-wrap ${
                status === 'error'
                  ? 'bg-red-50 text-red-600 dark:bg-red-950/30 dark:text-red-400'
                  : 'bg-[var(--color-bg-primary)] border border-border-default/40 text-text-secondary'
              }`}>
                {output}
              </pre>
            </div>
          )}
          {/* Cancelled：显式提示用户中断（无 output 时） */}
          {isCancelled && !output && (
            <div className="text-[11px] text-yellow-700 dark:text-yellow-500 flex items-center gap-1.5">
              <span>⏸</span>
              <span>用户中断了执行（结果不可用）</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
