/**
 * 统一下拉菜单组件
 *
 * 触发器（trigger）+ 面板（children）模式：点击触发器展开/收起。
 *
 * 特性：
 * - 内置展开/收起动画（dropdown-enter/exit）
 * - 点击外部 / ESC 自动关闭
 * - 关闭动画完成后才卸载 DOM（复用 useModalAnimation 模式）
 * - placement 控制弹出方向（top/bottom）
 * - 自动跟随主题（用 token，不写死颜色）
 *
 * 子组件：
 * - <DropdownItem> 统一菜单项样式（hover/disabled/danger）
 * - <DropdownDivider> 分隔线
 *
 * @example
 * ```tsx
 * <Dropdown
 *   trigger={<Button variant="ghost">菜单</Button>}
 *   placement="bottom"
 *   align="end"
 * >
 *   <DropdownItem icon={<Edit />} onClick={handleEdit}>编辑</DropdownItem>
 *   <DropdownItem icon={<Share />} onClick={handleShare}>分享</DropdownItem>
 *   <DropdownDivider />
 *   <DropdownItem icon={<Trash />} onClick={handleDelete} variant="danger">
 *     删除
 *   </DropdownItem>
 * </Dropdown>
 * ```
 */

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
  type MouseEvent,
  type MouseEventHandler,
} from 'react';
import { cn } from '../../utils/cn';
import { useExitAnimation } from '../../hooks/useExitAnimation';

export type DropdownPlacement = 'top' | 'bottom';
export type DropdownAlign = 'start' | 'end';

export interface DropdownProps {
  /**
   * 触发元素 — 必须是单个 React 元素（如 <button>）
   * Dropdown 会通过 cloneElement 在它身上注入 onClick，
   * 不会包一层 div，避免事件冒泡和 a11y 问题
   */
  trigger: ReactElement<{ onClick?: MouseEventHandler<HTMLElement> }>;
  /** 菜单内容（推荐使用 DropdownItem） */
  children: ReactNode;
  /** 弹出方向 */
  placement?: DropdownPlacement;
  /** 对齐方向（start = 左对齐 / end = 右对齐） */
  align?: DropdownAlign;
  /** 菜单宽度，默认 auto */
  menuClassName?: string;
}

/** 退出动画时长，与 animations.css 的 dropdown-exit 一致（--duration-fast = 100ms） */
const EXIT_DURATION = 100;

export function Dropdown({
  trigger,
  children,
  placement = 'bottom',
  align = 'start',
  menuClassName,
}: DropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // 退出动画状态机（统一复用 useExitAnimation Hook）
  const { shouldRender, isClosing } = useExitAnimation(isOpen, EXIT_DURATION);

  const close = useCallback(() => setIsOpen(false), []);

  const toggle = useCallback(
    (e: MouseEvent<HTMLElement>) => {
      // 调用原触发器自身的 onClick（如果有）
      if (isValidElement(trigger)) {
        const props = trigger.props as { onClick?: MouseEventHandler<HTMLElement> };
        props.onClick?.(e);
      }
      setIsOpen((prev) => !prev);
    },
    [trigger],
  );

  // 点击外部关闭
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: globalThis.MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        close();
      }
    };

    // 使用 mousedown 避免 click 事件冒泡问题
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen, close]);

  // ESC 关闭
  useEffect(() => {
    if (!isOpen) return;

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };

    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [isOpen, close]);

  // 用 cloneElement 把 onClick 注入到 trigger 元素本身
  // 这样 trigger 是 button 就保持是 button，不需要外层 div 包裹
  // a11y 自动正确（button 自带 role/键盘交互）
  const triggerWithHandler = cloneElement(trigger, { onClick: toggle });

  return (
    <div ref={containerRef} className="relative inline-block">
      {triggerWithHandler}

      {shouldRender && (
        <div
          role="menu"
          className={cn(
            'absolute min-w-[160px] py-1 z-30',
            'bg-surface-card border border-border-default rounded-lg shadow-lg',
            placement === 'bottom' ? 'top-full mt-1' : 'bottom-full mb-1',
            align === 'start' ? 'left-0' : 'right-0',
            isClosing ? 'animate-dropdown-exit' : 'animate-dropdown-enter',
            menuClassName,
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

// ==================== 子组件：DropdownItem ====================

export type DropdownItemVariant = 'default' | 'danger';

export interface DropdownItemProps {
  /** 点击回调 */
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void;
  /** 前置图标 */
  icon?: ReactNode;
  /** 后置内容（如箭头/快捷键） */
  trailing?: ReactNode;
  /** 风格变体 */
  variant?: DropdownItemVariant;
  /** 是否禁用 */
  disabled?: boolean;
  children: ReactNode;
}

const ITEM_VARIANT_CLASSES: Record<DropdownItemVariant, string> = {
  default: 'text-text-secondary hover:bg-hover hover:text-text-primary',
  danger: 'text-error hover:bg-error-light',
};

export function DropdownItem({
  onClick,
  icon,
  trailing,
  variant = 'default',
  disabled = false,
  children,
}: DropdownItemProps) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'w-full px-4 py-2 text-left text-sm',
        'flex items-center gap-3',
        'transition-base',
        'disabled:opacity-50 disabled:pointer-events-none',
        ITEM_VARIANT_CLASSES[variant],
      )}
    >
      {icon && <span className="inline-flex shrink-0 w-4 h-4">{icon}</span>}
      <span className="flex-1 truncate">{children}</span>
      {trailing && <span className="inline-flex shrink-0">{trailing}</span>}
    </button>
  );
}

// ==================== 子组件：DropdownDivider ====================

export function DropdownDivider() {
  return <div className="my-1 h-px bg-border-default" role="separator" />;
}
