/**
 * 工作区单个文件/文件夹条目
 *
 * 支持列表模式和图标模式两种渲染。
 * 交互：单击选中、双击打开、右键菜单（由 FileContextMenu 包裹）。
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { Folder } from 'lucide-react';
import { cn } from '../../utils/cn';
import { getFileIcon, getFileIconColor, formatFileSize } from '../../utils/fileUtils';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';

interface WorkspaceFileItemProps {
  item: FileItemData;
  /** 当前目录路径（用于拼完整路径） */
  currentPath: string;
  mode: 'list' | 'grid';
  /** 是否选中 */
  selected?: boolean;
  /** 单击回调（选中用，由父组件处理 Ctrl/Shift） */
  onSelect?: (path: string, e: React.MouseEvent) => void;
  /** 双击回调（打开文件/进入文件夹） */
  onOpen: (item: FileItemData) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  /** 开始重命名（外部触发，如右键菜单/F2） */
  startRename?: boolean;
  /** 重命名结束通知父组件 */
  onRenameEnd?: () => void;
}

/** 计算文件的完整相对路径 */
export function getFullPath(currentPath: string, name: string): string {
  return currentPath === '.' ? name : `${currentPath}/${name}`;
}

/** 格式化时间戳 */
function formatTime(modified: string): string {
  if (!modified) return '';
  try {
    const date = new Date(Number(modified) * 1000);
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    const hh = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:${min}`;
  } catch {
    return '';
  }
}

export default function WorkspaceFileItem({
  item,
  currentPath,
  mode,
  selected = false,
  onSelect,
  onOpen,
  onRename,
  startRename = false,
  onRenameEnd,
}: WorkspaceFileItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [newName, setNewName] = useState(item.name);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const renameSubmittingRef = useRef(false);

  // 外部触发重命名（右键菜单 / F2）
  useEffect(() => {
    if (startRename && !isRenaming) {
      setIsRenaming(true);
      setNewName(item.name);
    }
  }, [startRename, isRenaming, item.name]);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      const dotIndex = newName.lastIndexOf('.');
      renameInputRef.current?.setSelectionRange(0, dotIndex > 0 ? dotIndex : newName.length);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRenaming]);

  const handleClick = (e: React.MouseEvent) => {
    if (isRenaming) return;
    e.stopPropagation();
    const fullPath = getFullPath(currentPath, item.name);
    onSelect?.(fullPath, e);
  };

  const handleDoubleClick = () => {
    if (isRenaming) return;
    onOpen(item);
  };

  const handleRenameSubmit = useCallback(async () => {
    if (renameSubmittingRef.current) return;
    const trimmed = newName.trim();
    if (!trimmed || trimmed === item.name) {
      setIsRenaming(false);
      setNewName(item.name);
      onRenameEnd?.();
      return;
    }
    renameSubmittingRef.current = true;
    try {
      const success = await onRename(item.name, trimmed);
      if (!success) setNewName(item.name);
      setIsRenaming(false);
      onRenameEnd?.();
    } finally {
      renameSubmittingRef.current = false;
    }
  }, [newName, item.name, onRename, onRenameEnd]);

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    e.stopPropagation();
    if (e.key === 'Enter') handleRenameSubmit();
    if (e.key === 'Escape') {
      setIsRenaming(false);
      setNewName(item.name);
      onRenameEnd?.();
    }
  };

  const isUploading = item.uploadProgress !== undefined;

  // 选中态样式
  const selectedClass = selected && !isUploading
    ? 'bg-[var(--s-accent)]/10 border-l-2 border-l-[var(--s-accent)]'
    : '';

  // === 列表模式 ===
  if (mode === 'list') {
    return (
      <div
        className={cn(
          "group flex items-center gap-3 px-3 py-2.5 rounded-[var(--s-radius-control)] transition-colors",
          isUploading ? 'opacity-70' : 'hover:bg-[var(--s-hover)] cursor-pointer',
          selectedClass,
        )}
        onClick={isUploading ? undefined : handleClick}
        onDoubleClick={isUploading ? undefined : handleDoubleClick}
      >
        {/* 图标 */}
        <span className={cn('text-xl shrink-0', item.is_dir ? 'text-blue-500 dark:text-blue-400' : getFileIconColor(item.name))}>
          {item.is_dir ? <Folder className="w-5 h-5 fill-current" /> : getFileIcon(item.name)}
        </span>

        {/* 文件名 + 进度 */}
        <div className="flex-1 min-w-0">
          {isRenaming ? (
            <input
              ref={renameInputRef}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onBlur={handleRenameSubmit}
              onKeyDown={handleRenameKeyDown}
              onClick={(e) => e.stopPropagation()}
              className="w-full px-1.5 py-0.5 text-sm bg-[var(--s-surface-base)] border border-[var(--s-border-focus)] rounded outline-none text-[var(--s-text-primary)]"
            />
          ) : (
            <span className="text-sm text-[var(--s-text-primary)] truncate block">
              {item.name}{item.is_dir && '/'}
            </span>
          )}
          {isUploading && (
            <div className="mt-1 h-1 w-full bg-[var(--s-border-subtle)] rounded-full overflow-hidden">
              <div
                className="h-full bg-[var(--s-accent)] rounded-full transition-[width] duration-200"
                style={{ width: `${item.uploadProgress}%` }}
              />
            </div>
          )}
        </div>

        {/* 大小 / 进度百分比 */}
        <span className="text-xs text-[var(--s-text-tertiary)] w-16 text-right shrink-0">
          {isUploading ? `${item.uploadProgress}%` : !item.is_dir && formatFileSize(item.size)}
        </span>

        {/* 修改时间 */}
        <span className="text-xs text-[var(--s-text-tertiary)] w-24 text-right shrink-0">
          {isUploading ? '上传中...' : formatTime(item.modified)}
        </span>
      </div>
    );
  }

  // === 图标模式 ===
  return (
    <div
      className={cn(
        "group relative flex flex-col items-center gap-2 p-3 rounded-[var(--s-radius-card)] transition-colors",
        isUploading ? 'opacity-70' : 'hover:bg-[var(--s-hover)] cursor-pointer',
        selected && !isUploading && 'bg-[var(--s-accent)]/10 ring-1 ring-[var(--s-accent)]/30',
      )}
      onClick={isUploading ? undefined : handleClick}
      onDoubleClick={isUploading ? undefined : handleDoubleClick}
    >
      {/* 大图标 */}
      <span className={cn('text-4xl', item.is_dir ? 'text-blue-500 dark:text-blue-400' : getFileIconColor(item.name))}>
        {item.is_dir ? <Folder className="w-10 h-10 fill-current" /> : getFileIcon(item.name)}
      </span>

      {/* 文件名 */}
      {isRenaming ? (
        <input
          ref={renameInputRef}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onBlur={handleRenameSubmit}
          onKeyDown={handleRenameKeyDown}
          onClick={(e) => e.stopPropagation()}
          className="w-full px-1 py-0.5 text-xs text-center bg-[var(--s-surface-base)] border border-[var(--s-border-focus)] rounded outline-none text-[var(--s-text-primary)]"
        />
      ) : (
        <span className="text-xs text-[var(--s-text-primary)] text-center truncate w-full px-1">
          {item.name}
        </span>
      )}

      {/* 进度条 / 大小 */}
      {isUploading ? (
        <div className="w-full px-1">
          <div className="h-1 w-full bg-[var(--s-border-subtle)] rounded-full overflow-hidden">
            <div
              className="h-full bg-[var(--s-accent)] rounded-full transition-[width] duration-200"
              style={{ width: `${item.uploadProgress}%` }}
            />
          </div>
          <span className="text-[10px] text-[var(--s-text-tertiary)] block text-center mt-0.5">
            {item.uploadProgress}%
          </span>
        </div>
      ) : !item.is_dir && (
        <span className="text-[10px] text-[var(--s-text-tertiary)]">
          {formatFileSize(item.size)}
        </span>
      )}
    </div>
  );
}
