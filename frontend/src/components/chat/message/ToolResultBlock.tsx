/**
 * 工具结果独立渲染块
 *
 * 子 Agent（如 erp_agent）返回的结论文本，作为独立 content block 渲染。
 * 不会被主 Agent 的后续文本覆盖——主 Agent 文本显示在此块下方。
 * 文件下载按钮直接内联渲染（不等 message_done 的 FilePart）。
 */

import { memo } from 'react';
import type { FilePart } from '../../../types/message';
import MarkdownRenderer from './MarkdownRenderer';
import FileCardList from '../media/FileCard';

/** 工具名 → 展示标题 */
const TOOL_LABELS: Record<string, string> = {
  erp_agent: 'ERP 查询结果',
};

interface ToolResultBlockProps {
  toolName: string;
  text: string;
  files?: Array<{ url: string; name: string; mime_type: string; size?: number }>;
  isStreaming?: boolean;
}

export default memo(function ToolResultBlock({
  toolName,
  text,
  files,
  isStreaming = false,
}: ToolResultBlockProps) {
  const label = TOOL_LABELS[toolName] || toolName;

  return (
    <div className="my-2">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-text-secondary">
        <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 12.5a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11zm.75-8.25a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0zM8 7a.75.75 0 0 0-.75.75v3.5a.75.75 0 0 0 1.5 0v-3.5A.75.75 0 0 0 8 7z" />
        </svg>
        {label}
      </div>
      <MarkdownRenderer content={text} isStreaming={isStreaming} />
      {files && files.length > 0 && (
        <FileCardList files={files as FilePart[]} />
      )}
    </div>
  );
});
