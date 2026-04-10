/**
 * 对话项下拉菜单组件
 * 包含重命名、置顶、分享、批量管理、移动到分组、导出、删除等选项
 */

import { Edit2, Pin, Share2, List, FolderInput, Download, Trash2, ChevronRight } from 'lucide-react';
import styles from './shared.module.css';

/** 菜单项统一样式 */
const MENU_ITEM_CLASS =
  'w-full px-4 py-2 text-left text-sm text-text-secondary hover:bg-hover hover:text-text-primary flex items-center gap-3 transition-base';

interface DropdownMenuProps {
  x: number;
  y: number;
  closing?: boolean;
  onRename: () => void;
  onPin?: () => void;
  onShare?: () => void;
  onBatchManage?: () => void;
  onMoveToGroup?: () => void;
  onExport?: () => void;
  onDelete: () => void;
}

export default function DropdownMenu({
  x,
  y,
  closing = false,
  onRename,
  onPin,
  onShare,
  onBatchManage,
  onMoveToGroup,
  onExport,
  onDelete,
}: DropdownMenuProps) {
  return (
    <div
      className={`${styles['dropdown-menu']} bg-surface-card rounded-lg shadow-lg border border-border-default py-1 z-30 w-44 origin-top-right ${
        closing ? 'animate-dropdown-exit' : 'animate-dropdown-enter'
      }`}
      // 动态定位需要 CSS 变量，忽略内联样式警告
      style={
        {
          '--menu-x': `${x}px`,
          '--menu-y': `${y}px`,
        } as React.CSSProperties
      }
      onClick={(e) => e.stopPropagation()}
    >
      {/* 重命名 */}
      <button
        type="button"
        onClick={onRename}
        className={MENU_ITEM_CLASS}
      >
        <Edit2 className="w-4 h-4" />
        <span>重命名</span>
      </button>

      {/* 置顶此对话 */}
      {onPin && (
        <button
          type="button"
          onClick={onPin}
          className={MENU_ITEM_CLASS}
        >
          <Pin className="w-4 h-4" />
          <span>置顶此对话</span>
        </button>
      )}

      {/* 分享此对话 */}
      {onShare && (
        <button
          type="button"
          onClick={onShare}
          className={MENU_ITEM_CLASS}
        >
          <Share2 className="w-4 h-4" />
          <span>分享此对话</span>
        </button>
      )}

      {/* 批量管理 */}
      {onBatchManage && (
        <button
          type="button"
          onClick={onBatchManage}
          className={MENU_ITEM_CLASS}
        >
          <List className="w-4 h-4" />
          <span>批量管理</span>
        </button>
      )}

      {/* 移动到分组 */}
      {onMoveToGroup && (
        <button
          type="button"
          onClick={onMoveToGroup}
          className={`${MENU_ITEM_CLASS} justify-between`}
        >
          <div className="flex items-center gap-3">
            <FolderInput className="w-4 h-4" />
            <span>移动到分组</span>
          </div>
          <ChevronRight className="w-4 h-4" />
        </button>
      )}

      {/* 导出会话 */}
      {onExport && (
        <button
          type="button"
          onClick={onExport}
          className={`${MENU_ITEM_CLASS} justify-between`}
        >
          <div className="flex items-center gap-3">
            <Download className="w-4 h-4" />
            <span>导出会话</span>
          </div>
          <ChevronRight className="w-4 h-4" />
        </button>
      )}

      {/* 分隔线 */}
      <div className="my-1 border-t border-border-default"></div>

      {/* 删除此对话 (红色) */}
      <button
        type="button"
        onClick={onDelete}
        className="w-full px-4 py-2 text-left text-sm text-error hover:bg-error-light flex items-center gap-3 transition-base"
      >
        <Trash2 className="w-4 h-4" />
        <span>删除此对话</span>
      </button>
    </div>
  );
}
