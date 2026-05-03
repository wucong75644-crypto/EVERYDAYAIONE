/**
 * @ 文件提及下拉面板
 *
 * 在输入框上方弹出，显示搜索结果列表。
 * UI 参考 Claude Code 的 @file 选择器风格。
 */

import { useEffect, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { getFileIcon } from '../../../utils/fileUtils';
import type { MentionResult } from '../../../hooks/useFileMention';

interface FileMentionDropdownProps {
  results: MentionResult[];
  activeIndex: number;
  loading: boolean;
  onSelect: (file: MentionResult) => void;
  onHover: (index: number) => void;
}

export default function FileMentionDropdown({
  results,
  activeIndex,
  loading,
  onSelect,
  onHover,
}: FileMentionDropdownProps) {
  const listRef = useRef<HTMLDivElement>(null);

  // 滚动高亮项到可见区域
  useEffect(() => {
    const container = listRef.current;
    if (!container) return;
    const activeEl = container.children[activeIndex] as HTMLElement | undefined;
    activeEl?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  // 无结果且非加载中：不渲染面板
  if (!loading && results.length === 0) return null;

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 z-50">
      <div className="rounded-xl border border-border-default bg-surface-card shadow-lg overflow-hidden">
        {loading && results.length === 0 ? (
          <div className="flex items-center gap-2 px-4 py-3 text-sm text-text-tertiary">
            <Loader2 className="w-4 h-4 animate-spin" />
            搜索中...
          </div>
        ) : (
          <div ref={listRef} className="max-h-[240px] overflow-y-auto py-1">
            {results.map((file, index) => (
              <button
                key={file.workspace_path}
                type="button"
                className={cn(
                  'w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors',
                  index === activeIndex
                    ? 'bg-accent text-text-on-accent'
                    : 'hover:bg-surface-hover text-text-primary',
                )}
                onMouseEnter={() => onHover(index)}
                onMouseDown={(e) => {
                  e.preventDefault(); // 阻止 textarea 失焦
                  onSelect(file);
                }}
              >
                <span className="text-base shrink-0">{getFileIcon(file.name)}</span>
                <span className="truncate text-sm font-medium">{file.name}</span>
                {/* 显示路径（如果文件在子目录中） */}
                {file.workspace_path !== file.name && (
                  <span className={cn(
                    'ml-auto text-xs truncate max-w-[200px] shrink-0',
                    index === activeIndex ? 'text-text-on-accent/70' : 'text-text-tertiary',
                  )}>
                    {file.workspace_path}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
