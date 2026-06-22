/**
 * 工作区右键上下文菜单
 *
 * 基于 Radix ContextMenu，样式复用 DropdownMenu token。
 * 两种模式：文件菜单（右键文件）、空白菜单（右键空白区域）。
 */

import { type ReactNode } from 'react';
import * as RadixContextMenu from '@radix-ui/react-context-menu';
import { FolderOpen, Pencil, Download, MessageSquarePlus, Trash2, FolderPlus, Upload } from 'lucide-react';
import { cn } from '../../utils/cn';

// ============================================================
// 菜单项原语（复用 DropdownMenu 的样式 token）
// ============================================================

function MenuItem({
  icon,
  shortcut,
  variant = 'default',
  children,
  ...rest
}: RadixContextMenu.ContextMenuItemProps & {
  icon?: ReactNode;
  shortcut?: string;
  variant?: 'default' | 'danger';
}) {
  return (
    <RadixContextMenu.Item
      className={cn(
        'flex items-center gap-2.5 px-3 py-2',
        'text-sm select-none outline-none cursor-pointer',
        'transition-colors duration-[var(--a-duration-fast)]',
        'data-[highlighted]:bg-[var(--c-dropdown-item-hover)]',
        'data-[disabled]:opacity-50 data-[disabled]:pointer-events-none',
        variant === 'default' && 'text-[var(--s-text-primary)]',
        variant === 'danger' && 'text-[var(--s-error)] data-[highlighted]:bg-[var(--s-error-soft)]',
      )}
      {...rest}
    >
      {icon && <span className="inline-flex shrink-0 w-4 h-4">{icon}</span>}
      <span className="flex-1">{children}</span>
      {shortcut && <span className="text-xs text-[var(--s-text-tertiary)] ml-4">{shortcut}</span>}
    </RadixContextMenu.Item>
  );
}

function Separator() {
  return <RadixContextMenu.Separator className="h-px my-1 bg-[var(--s-border-default)]" />;
}

// ============================================================
// 文件右键菜单
// ============================================================

interface FileMenuProps {
  isDir: boolean;
  hasCdnUrl: boolean;
  selectedCount: number;
  onOpen: () => void;
  onRename: () => void;
  onDownload: () => void;
  onBatchDownload?: () => void;
  onSendToChat: () => void;
  onDelete: () => void;
}

function FileMenu({ isDir, hasCdnUrl, selectedCount, onOpen, onRename, onDownload, onBatchDownload, onSendToChat, onDelete }: FileMenuProps) {
  const isBatch = selectedCount > 1;
  return (
    <>
      {!isBatch && <MenuItem icon={<FolderOpen className="w-4 h-4" />} onSelect={onOpen}>打开</MenuItem>}
      {!isBatch && <MenuItem icon={<Pencil className="w-4 h-4" />} shortcut="F2" onSelect={onRename}>重命名</MenuItem>}
      {/* 批量：下载选中 */}
      {isBatch && onBatchDownload && (
        <MenuItem icon={<Download className="w-4 h-4" />} onSelect={onBatchDownload}>
          下载选中 ({selectedCount})
        </MenuItem>
      )}
      {/* 单选文件：原下载 */}
      {!isBatch && !isDir && hasCdnUrl && (
        <MenuItem icon={<Download className="w-4 h-4" />} onSelect={onDownload}>下载</MenuItem>
      )}
      {/* 单选文件夹：ZIP 下载 */}
      {!isBatch && isDir && onBatchDownload && (
        <MenuItem icon={<Download className="w-4 h-4" />} onSelect={onBatchDownload}>下载（ZIP）</MenuItem>
      )}
      {!isBatch && !isDir && (
        <MenuItem icon={<MessageSquarePlus className="w-4 h-4" />} onSelect={onSendToChat}>插入到聊天</MenuItem>
      )}
      <Separator />
      <MenuItem icon={<Trash2 className="w-4 h-4" />} variant="danger" shortcut="Del" onSelect={onDelete}>
        {isBatch ? `删除 ${selectedCount} 项` : '删除'}
      </MenuItem>
    </>
  );
}

// ============================================================
// 空白区域右键菜单
// ============================================================

interface BlankMenuProps {
  onNewFolder: () => void;
  onUpload: () => void;
}

function BlankMenu({ onNewFolder, onUpload }: BlankMenuProps) {
  return (
    <>
      <MenuItem icon={<FolderPlus className="w-4 h-4" />} onSelect={onNewFolder}>新建文件夹</MenuItem>
      <MenuItem icon={<Upload className="w-4 h-4" />} onSelect={onUpload}>上传文件</MenuItem>
    </>
  );
}

// ============================================================
// 主组件
// ============================================================

interface FileContextMenuProps {
  children: ReactNode;
  /** 菜单类型：file=文件右键，blank=空白区域右键 */
  type: 'file' | 'blank';
  /** 文件菜单 props（type=file 时必传） */
  fileProps?: FileMenuProps;
  /** 空白菜单 props（type=blank 时必传） */
  blankProps?: BlankMenuProps;
}

export default function FileContextMenu({ children, type, fileProps, blankProps }: FileContextMenuProps) {
  return (
    <RadixContextMenu.Root>
      <RadixContextMenu.Trigger asChild>
        {children}
      </RadixContextMenu.Trigger>
      <RadixContextMenu.Portal>
        <RadixContextMenu.Content
          className={cn(
            'z-50 overflow-hidden',
            'rounded-[var(--c-dropdown-radius)]',
            'border border-[var(--c-dropdown-border)]',
            'bg-[var(--c-dropdown-bg)]',
            'shadow-[var(--c-dropdown-shadow)]',
            'py-1 min-w-[180px]',
          )}
          collisionPadding={8}
        >
          {type === 'file' && fileProps && <FileMenu {...fileProps} />}
          {type === 'blank' && blankProps && <BlankMenu {...blankProps} />}
        </RadixContextMenu.Content>
      </RadixContextMenu.Portal>
    </RadixContextMenu.Root>
  );
}
