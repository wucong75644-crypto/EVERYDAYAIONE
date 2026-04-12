/**
 * 工作区单个文件/文件夹条目
 *
 * 支持列表模式和图标模式两种渲染。
 * 操作菜单：重命名、删除、插入到聊天、移动到（待 Phase 4）。
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { Folder, MoreHorizontal, Pencil, Trash2, MessageSquarePlus, Download } from 'lucide-react';
import { Dropdown, DropdownItem, DropdownDivider } from '../ui/Dropdown';
import { Button } from '../ui/Button';
import { cn } from '../../utils/cn';
import { getFileIcon, getFileIconColor, formatFileSize } from '../../utils/fileUtils';
import { downloadFile } from '../../utils/downloadFile';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';

interface WorkspaceFileItemProps {
  item: FileItemData;
  /** 当前目录路径（用于拼完整路径） */
  currentPath: string;
  mode: 'list' | 'grid';
  onNavigate: (path: string) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  onDelete: (path: string) => void;
  onPreview: (item: FileItemData) => void;
  onSendToChat?: (item: FileItemData) => void;
}

/** 计算文件的完整相对路径 */
function getFullPath(currentPath: string, name: string): string {
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
  onNavigate,
  onRename,
  onDelete,
  onPreview,
  onSendToChat,
}: WorkspaceFileItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [newName, setNewName] = useState(item.name);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const renameSubmittingRef = useRef(false);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      // 选中文件名（不含扩展名）
      const dotIndex = newName.lastIndexOf('.');
      renameInputRef.current?.setSelectionRange(0, dotIndex > 0 ? dotIndex : newName.length);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在 isRenaming 变化时执行
  }, [isRenaming]);

  const handleClick = () => {
    if (isRenaming) return;
    if (item.is_dir) {
      onNavigate(getFullPath(currentPath, item.name));
    } else {
      onPreview(item);
    }
  };

  const handleRenameSubmit = useCallback(async () => {
    if (renameSubmittingRef.current) return; // 防止 blur+Enter 双提交
    const trimmed = newName.trim();
    if (!trimmed || trimmed === item.name) {
      setIsRenaming(false);
      setNewName(item.name);
      return;
    }
    renameSubmittingRef.current = true;
    try {
      const success = await onRename(item.name, trimmed);
      if (success) {
        setIsRenaming(false);
      } else {
        setNewName(item.name);
        setIsRenaming(false);
      }
    } finally {
      renameSubmittingRef.current = false;
    }
  }, [newName, item.name, onRename]);

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleRenameSubmit();
    if (e.key === 'Escape') {
      setIsRenaming(false);
      setNewName(item.name);
    }
  };

  const handleDownload = () => {
    if (item.cdn_url) {
      downloadFile(item.cdn_url, item.name);
    }
  };

  const fullPath = getFullPath(currentPath, item.name);

  const menuTrigger = (
    <Button
      variant="ghost"
      size="sm"
      className="!p-1 opacity-0 group-hover:opacity-100 transition-opacity"
      aria-label="更多操作"
    >
      <MoreHorizontal className="w-4 h-4" />
    </Button>
  );

  const dropdownMenu = (
    <Dropdown trigger={menuTrigger} placement="bottom" align="end">
      <DropdownItem icon={<Pencil className="w-4 h-4" />} onClick={() => { setIsRenaming(true); setNewName(item.name); }}>
        重命名
      </DropdownItem>
      {!item.is_dir && item.cdn_url && (
        <DropdownItem icon={<Download className="w-4 h-4" />} onClick={handleDownload}>
          下载
        </DropdownItem>
      )}
      {!item.is_dir && onSendToChat && (
        <DropdownItem icon={<MessageSquarePlus className="w-4 h-4" />} onClick={() => onSendToChat(item)}>
          插入到聊天
        </DropdownItem>
      )}
      <DropdownDivider />
      <DropdownItem icon={<Trash2 className="w-4 h-4" />} variant="danger" onClick={() => onDelete(fullPath)}>
        删除
      </DropdownItem>
    </Dropdown>
  );

  // === 列表模式 ===
  if (mode === 'list') {
    return (
      <div
        className="group flex items-center gap-3 px-3 py-2.5 hover:bg-[var(--s-hover)] rounded-[var(--s-radius-control)] cursor-pointer transition-colors"
        onClick={handleClick}
        onDoubleClick={() => { if (!item.is_dir) { setIsRenaming(true); setNewName(item.name); } }}
      >
        {/* 图标 */}
        <span className={cn('text-xl shrink-0', item.is_dir ? 'text-blue-500 dark:text-blue-400' : getFileIconColor(item.name))}>
          {item.is_dir ? <Folder className="w-5 h-5 fill-current" /> : getFileIcon(item.name)}
        </span>

        {/* 文件名 */}
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
        </div>

        {/* 大小 */}
        <span className="text-xs text-[var(--s-text-tertiary)] w-16 text-right shrink-0">
          {!item.is_dir && formatFileSize(item.size)}
        </span>

        {/* 修改时间 */}
        <span className="text-xs text-[var(--s-text-tertiary)] w-24 text-right shrink-0">
          {formatTime(item.modified)}
        </span>

        {/* 操作菜单 */}
        <div onClick={(e) => e.stopPropagation()}>
          {dropdownMenu}
        </div>
      </div>
    );
  }

  // === 图标模式 ===
  return (
    <div
      className="group relative flex flex-col items-center gap-2 p-3 rounded-[var(--s-radius-card)] hover:bg-[var(--s-hover)] cursor-pointer transition-colors"
      onClick={handleClick}
      onDoubleClick={() => { if (!item.is_dir) { setIsRenaming(true); setNewName(item.name); } }}
    >
      {/* 操作按钮（右上角） */}
      <div className="absolute top-1 right-1" onClick={(e) => e.stopPropagation()}>
        {dropdownMenu}
      </div>

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

      {/* 大小 */}
      {!item.is_dir && (
        <span className="text-[10px] text-[var(--s-text-tertiary)]">
          {formatFileSize(item.size)}
        </span>
      )}
    </div>
  );
}
