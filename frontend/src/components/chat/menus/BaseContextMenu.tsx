/**
 * 右键菜单"壳"组件
 *
 * 把所有右键菜单共用的逻辑收口在这里：
 * - fixed 定位 + 防超视口
 * - ESC / 点击菜单外关闭
 * - 入/出场动画 class
 * - 容器样式 + items 渲染循环
 *
 * 调用方只负责准备 items 数组和各自的业务回调（引用图片、复制文字、删除会话等）。
 */

import { useEffect, useRef } from 'react';
import type { LucideIcon } from 'lucide-react';

/** 单项色调（决定文字色 + hover 背景） */
type ItemTone = 'accent' | 'secondary' | 'danger';

export interface ContextMenuItem {
  label: string;
  icon?: LucideIcon;
  onClick: () => void;
  tone?: ItemTone;
}

interface BaseContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  closing?: boolean;
  onClose: () => void;
}

const TONE_CLASS: Record<ItemTone, string> = {
  accent: 'text-accent hover:bg-hover',
  secondary: 'text-text-secondary hover:bg-hover hover:text-text-primary',
  danger: 'text-error hover:bg-error-light',
};

/** 单项估算高度（py-2 + text-sm + 上下间距），用于防超视口 */
const ITEM_HEIGHT = 40;
const MENU_PADDING_Y = 8;
const MENU_WIDTH = 140;

export default function BaseContextMenu({
  x,
  y,
  items,
  closing = false,
  onClose,
}: BaseContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [onClose]);

  const menuHeight = items.length * ITEM_HEIGHT + MENU_PADDING_Y;
  const adjustedX = x + MENU_WIDTH > window.innerWidth ? window.innerWidth - MENU_WIDTH - 8 : x;
  const adjustedY = y + menuHeight > window.innerHeight ? window.innerHeight - menuHeight - 8 : y;

  return (
    <div
      ref={menuRef}
      className={`fixed bg-surface-card rounded-lg shadow-lg border border-border-default py-1 z-30 min-w-32 ${
        closing ? 'animate-dropdown-exit' : 'animate-dropdown-enter'
      }`}
      style={{ left: `${adjustedX}px`, top: `${adjustedY}px` }}
      onClick={(e) => e.stopPropagation()}
    >
      {items.map(({ label, icon: Icon, onClick, tone = 'secondary' }) => (
        <button
          key={label}
          onClick={onClick}
          className={`w-full px-4 py-2 text-left text-sm flex items-center gap-2 transition-base ${TONE_CLASS[tone]}`}
        >
          {Icon && <Icon className="w-4 h-4" />}
          {label}
        </button>
      ))}
    </div>
  );
}
