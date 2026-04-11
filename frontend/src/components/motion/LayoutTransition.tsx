/**
 * LayoutTransition — FLIP 布局动画封装
 *
 * 基于 framer-motion 的 `layout` prop，让元素在布局变化时（位置/尺寸）
 * 自动过渡到新状态，而不是瞬间跳变。
 *
 * 典型场景：
 * - 列表重排（排序/过滤）
 * - Sidebar 收起/展开，兄弟元素自动移动到新位置
 * - 卡片从一格展开到全屏详情
 *
 * 注意事项：
 * - `layout` 动画会抑制 transform 类的动画，建议不要在同一元素上叠加
 * - 大量元素（>100）的 layout 动画性能差，用 shouldAnimate 阈值控制
 * - 文字内容会有轻微拉伸抖动，可用 `layout="position"` 只动位置不动尺寸
 *
 * @example
 * ```tsx
 * <LayoutTransition as="li" key={item.id}>
 *   {content}
 * </LayoutTransition>
 *
 * // 配合 AnimatePresence 做进出场：
 * <AnimatePresence mode="popLayout">
 *   {items.map(item => (
 *     <LayoutTransition key={item.id}>
 *       {item.name}
 *     </LayoutTransition>
 *   ))}
 * </AnimatePresence>
 * ```
 */

import { type ReactNode } from 'react';
import { m, type TargetAndTransition, type VariantLabels } from 'framer-motion';
import { SOFT_SPRING } from '../../utils/motion';

type MotionTarget = boolean | TargetAndTransition | VariantLabels;

interface LayoutTransitionProps {
  children: ReactNode;
  /** layout 模式：true（位置+尺寸）| 'position'（仅位置）| 'size'（仅尺寸） */
  layout?: true | 'position' | 'size';
  /** Magic Move 跨组件共享 layoutId */
  layoutId?: string;
  /** 是否应用 spring 过渡，false 时走默认时长 */
  spring?: boolean;
  /** 渲染的 HTML tag */
  as?: 'div' | 'li' | 'article';
  className?: string;
  /** 进出场（配合 AnimatePresence 用）— 复用 framer-motion 官方类型 */
  initial?: MotionTarget;
  animate?: MotionTarget;
  exit?: TargetAndTransition | VariantLabels;
}

export function LayoutTransition({
  children,
  layout = true,
  layoutId,
  spring = true,
  as = 'div',
  className,
  initial,
  animate,
  exit,
}: LayoutTransitionProps) {
  const transition = spring ? SOFT_SPRING : { duration: 0.25 };

  const commonProps = {
    className,
    layout,
    layoutId,
    transition,
    initial,
    animate,
    exit,
  };

  if (as === 'li') {
    return <m.li {...commonProps}>{children}</m.li>;
  }
  if (as === 'article') {
    return <m.article {...commonProps}>{children}</m.article>;
  }
  return <m.div {...commonProps}>{children}</m.div>;
}
