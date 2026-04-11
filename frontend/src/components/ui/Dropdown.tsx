/**
 * 统一下拉菜单组件（V3 — 基于 Radix primitive 薄封装）
 *
 * V3 重大升级：
 * - 内部实现从自研 useExitAnimation + click-outside 换成 primitives/DropdownMenu
 * - 底座是 Radix UI DropdownMenu：完整 a11y（键盘方向键/typeahead/焦点 trap）
 * - 动画是 framer-motion spring（替代 CSS keyframe）
 * - Portal 渲染避免父级 z-index 问题
 * - 外部 API 保留向后兼容（trigger prop + DropdownItem / DropdownDivider 子组件）
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

import { type ReactElement, type ReactNode, type MouseEventHandler } from 'react';
import {
  DropdownMenu as PrimitiveDropdownMenu,
  DropdownMenuItem as PrimitiveItem,
  DropdownMenuSeparator as PrimitiveSeparator,
} from '../primitives/DropdownMenu';
import { cn } from '../../utils/cn';

export type DropdownPlacement = 'top' | 'bottom';
export type DropdownAlign = 'start' | 'end';

export interface DropdownProps {
  /**
   * 触发元素 — 必须是单个 React 元素（如 <button>）
   * 沿用 V2 API：cloneElement 风格的 trigger prop
   */
  trigger: ReactElement<{ onClick?: MouseEventHandler<HTMLElement> }>;
  /** 菜单内容（推荐使用 DropdownItem） */
  children: ReactNode;
  /** 弹出方向 */
  placement?: DropdownPlacement;
  /** 对齐方向（start = 左对齐 / end = 右对齐） */
  align?: DropdownAlign;
  /** 菜单自定义 className（注入到 content） */
  menuClassName?: string;
}

export function Dropdown({
  trigger,
  children,
  placement = 'bottom',
  align = 'start',
  menuClassName: _menuClassName,
}: DropdownProps) {
  return (
    <PrimitiveDropdownMenu
      trigger={trigger}
      side={placement}
      align={align}
      minWidth={160}
    >
      {children}
    </PrimitiveDropdownMenu>
  );
}

// ==================== 子组件：DropdownItem ====================
//
// 向后兼容：旧 API 是 `onClick`，Radix primitive 用的是 `onSelect`。
// 这里做 onClick → onSelect 映射，保持外部 API 稳定。

export type DropdownItemVariant = 'default' | 'danger';

export interface DropdownItemProps {
  /** 点击回调（会映射到 Radix 的 onSelect） */
  onClick?: () => void;
  /** 前置图标 */
  icon?: ReactNode;
  /** 后置内容（如快捷键提示） */
  trailing?: ReactNode;
  /** 风格变体 */
  variant?: DropdownItemVariant;
  /** 是否禁用 */
  disabled?: boolean;
  children: ReactNode;
}

export function DropdownItem({
  onClick,
  icon,
  trailing,
  variant = 'default',
  disabled = false,
  children,
}: DropdownItemProps) {
  return (
    <PrimitiveItem
      icon={icon}
      variant={variant}
      disabled={disabled}
      onSelect={() => onClick?.()}
    >
      <span className="flex items-center justify-between w-full gap-3">
        <span className="flex-1 truncate">{children}</span>
        {trailing && <span className="inline-flex shrink-0">{trailing}</span>}
      </span>
    </PrimitiveItem>
  );
}

// ==================== 子组件：DropdownDivider ====================

export function DropdownDivider() {
  return <PrimitiveSeparator />;
}

// 给 cn unused warning 打补丁（未来可能需要 menuClassName 再用）
void cn;
