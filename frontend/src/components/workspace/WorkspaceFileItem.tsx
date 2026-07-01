/**
 * 工作区单个文件/文件夹条目
 *
 * 支持列表模式和图标模式两种渲染。
 * 交互：单击选中、双击打开、右键菜单（由 FileContextMenu 包裹）。
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { Folder, Check } from 'lucide-react';
import { cn } from '../../utils/cn';
import { getFileIcon, getFileIconColor, formatFileSize } from '../../utils/fileUtils';
import { IMAGE_EXTS } from '../../utils/fileCategory';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';

/** 中间省略：头部 + … + 尾部（含后缀前几个字符），对齐 macOS Finder */
function ellipsisMiddle(name: string, maxLen: number): string {
  if (name.length <= maxLen) return name;
  const dotIdx = name.lastIndexOf('.');
  const ext = dotIdx > 0 ? name.slice(dotIdx) : '';
  const stem = dotIdx > 0 ? name.slice(0, dotIdx) : name;
  // 尾部保留 stem 最后 4 个字符 + 后缀
  const tailLen = Math.min(4, stem.length);
  const tail = stem.slice(-tailLen) + ext;
  // 头部 = 总长 - 省略号(1) - 尾部
  const headLen = Math.max(1, maxLen - 1 - tail.length);
  return name.slice(0, headLen) + '…' + tail;
}

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
  /** 拖拽移动回调（将文件移入目标文件夹） */
  onMove?: (srcPath: string, destDir: string) => void;
  /** 当前选中的路径集合（拖拽多文件用） */
  selectedPaths?: Set<string>;
  /** 多选模式：grid 模式下渲染复选框 + 单击直接 toggle 选中 */
  multiSelectMode?: boolean;
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

// 自定义 MIME type 标识内部拖拽（区分外部文件上传）
const DRAG_MIME = 'application/x-workspace-move';

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
  onMove,
  selectedPaths,
  multiSelectMode = false,
}: WorkspaceFileItemProps) {
  const [isRenaming, setIsRenaming] = useState(false);
  const [newName, setNewName] = useState(item.name);
  const [isDragOver, setIsDragOver] = useState(false);
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

  const isUploading = item.uploadProgress !== undefined;
  const fullPath = getFullPath(currentPath, item.name);

  const handleClick = (e: React.MouseEvent) => {
    if (isRenaming) return;
    e.stopPropagation();
    onSelect?.(fullPath, e);
  };

  /** 双击名字 → 进入重命名（stopPropagation 阻止冒泡到父级的"打开"） */
  const handleNameDoubleClick = (e: React.MouseEvent) => {
    if (isUploading) return;
    e.stopPropagation();
    setIsRenaming(true);
    setNewName(item.name);
  };

  /** 双击图标/行其他区域 → 打开文件或进入文件夹 */
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

  // 拖拽：开始拖拽（文件作为拖拽源）
  const handleDragStart = (e: React.DragEvent) => {
    if (isUploading || isRenaming) { e.preventDefault(); return; }
    // 收集要拖拽的路径（如果当前文件在选中集合中，拖拽所有选中项；否则只拖拽当前文件）
    const paths = selectedPaths?.has(fullPath) && selectedPaths.size > 1
      ? Array.from(selectedPaths)
      : [fullPath];
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify(paths));
    e.dataTransfer.effectAllowed = 'move';
  };

  // 拖拽：文件夹作为放入目标
  const handleDragOver = (e: React.DragEvent) => {
    if (!item.is_dir || !e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setIsDragOver(true);
  };

  const handleDragLeave = () => setIsDragOver(false);

  const handleDrop = (e: React.DragEvent) => {
    setIsDragOver(false);
    if (!item.is_dir || !onMove) return;
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (!raw) return;
    e.preventDefault();
    let paths: string[];
    try { paths = JSON.parse(raw); } catch { return; }
    const destDir = fullPath;
    for (const src of paths) {
      if (src !== destDir) onMove(src, destDir);
    }
  };

  // 拖拽 props（文件可拖，文件夹可接收）
  const dragProps = isUploading ? {} : {
    draggable: true,
    onDragStart: handleDragStart,
    ...(item.is_dir ? { onDragOver: handleDragOver, onDragLeave: handleDragLeave, onDrop: handleDrop } : {}),
  };

  // 选中态样式
  const selectedClass = selected && !isUploading
    ? 'bg-[var(--s-accent)]/10 border-l-2 border-l-[var(--s-accent)]'
    : '';
  // 拖拽放入目标高亮
  const dragOverClass = isDragOver ? 'bg-[var(--s-accent)]/15 border border-dashed border-[var(--s-accent)]' : '';

  // === 列表模式 ===
  if (mode === 'list') {
    return (
      <div
        data-workspace-path={fullPath}
        className={cn(
          "group flex items-center gap-3 px-3 py-2.5 rounded-[var(--s-radius-control)] transition-colors",
          isUploading ? 'opacity-70' : 'hover:bg-[var(--s-hover)] cursor-pointer',
          selectedClass,
          dragOverClass,
        )}
        onClick={isUploading ? undefined : handleClick}
        onDoubleClick={isUploading ? undefined : handleDoubleClick}
        {...dragProps}
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
              className="w-full px-1.5 py-0.5 text-sm bg-transparent border-none outline-none ring-1 ring-[var(--s-accent)] rounded text-[var(--s-text-primary)]"
            />
          ) : (
            <span
              className="text-sm text-[var(--s-text-primary)] truncate block"
              onDoubleClick={handleNameDoubleClick}
            >
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
      data-workspace-path={fullPath}
      className={cn(
        "group relative flex flex-col items-center gap-3 p-4 rounded-[var(--s-radius-card)] transition-colors",
        isUploading ? 'opacity-70' : 'hover:bg-[var(--s-hover)] cursor-pointer',
        selected && !isUploading && 'bg-[var(--s-accent)]/10 ring-1 ring-[var(--s-accent)]/30',
        dragOverClass,
      )}
      onClick={isUploading ? undefined : handleClick}
      {...dragProps}
      onDoubleClick={isUploading ? undefined : handleDoubleClick}
    >
      {/* 多选模式复选框（左上角，仅 grid 模式 + 多选模式时显示） */}
      {multiSelectMode && !isUploading && (
        <div
          aria-hidden
          className={cn(
            'absolute top-2 left-2 w-5 h-5 rounded border flex items-center justify-center transition-colors pointer-events-none',
            selected
              ? 'bg-[var(--s-accent)] border-[var(--s-accent)]'
              : 'bg-white/90 dark:bg-black/40 border-[var(--s-border-default)]',
          )}
        >
          {selected && <Check className="w-3.5 h-3.5 text-white" strokeWidth={3} />}
        </div>
      )}

      {/* 大图标 / 图片缩略图 */}
      {(() => {
        const isImage = !item.is_dir && !!item.cdn_url && IMAGE_EXTS.has((item.name.split('.').pop() || '').toLowerCase());
        return (
          <>
              {isImage && (
                <img
                src={item.thumbnail_url || item.cdn_url || ''}
                alt={item.name}
                className="w-[72px] h-[72px] rounded-lg object-cover"
                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; (e.target as HTMLImageElement).nextElementSibling?.classList.remove('hidden'); }}
              />
            )}
            <span className={cn(
              item.is_dir ? 'text-blue-500 dark:text-blue-400' : getFileIconColor(item.name),
              isImage && 'hidden',
            )}>
              {item.is_dir ? <Folder className="w-[72px] h-[72px] fill-current" /> : <span className="text-[56px] leading-none">{getFileIcon(item.name)}</span>}
            </span>
          </>
        );
      })()}

      {/* 文件名 */}
      {isRenaming ? (
        <input
          ref={renameInputRef}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onBlur={handleRenameSubmit}
          onKeyDown={handleRenameKeyDown}
          onClick={(e) => e.stopPropagation()}
          className="w-full px-1 py-0.5 text-[13px] text-center bg-transparent border-none outline-none ring-1 ring-[var(--s-accent)] rounded text-[var(--s-text-primary)] leading-[18px]"
        />
      ) : (
        <span
          className="text-[13px] text-[var(--s-text-primary)] text-center w-full px-1 leading-[18px]"
          onDoubleClick={handleNameDoubleClick}
          title={item.name}
        >
          {ellipsisMiddle(item.name, 20)}
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
        <span className="text-[11px] text-[var(--s-text-tertiary)]">
          {formatFileSize(item.size)}
        </span>
      )}
    </div>
  );
}
