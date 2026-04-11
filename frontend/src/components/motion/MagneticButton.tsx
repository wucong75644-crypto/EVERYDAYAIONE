/**
 * MagneticButton — 磁吸按钮
 *
 * 鼠标靠近时按钮轻微"吸附"向光标方向移动，离开回弹。
 * 用在首页 Hero 的 CTA 按钮，增加"手感"吸引力。
 *
 * 实现：监听 mousemove，计算鼠标与按钮中心的偏移量。
 *
 * V3 Review Fix — 性能优化：
 * - 旧版用 useState 存 offset，每次 mousemove (60~120Hz) setState → React rerender
 * - 新版用 framer useMotionValue + useSpring，直接绕过 React，
 *   值变化只会触发 framer 内部的 transform 写入，零 React rerender
 *
 * @example
 * ```tsx
 * <MagneticButton strength={0.4}>
 *   <Button variant="accent" size="lg">开始使用</Button>
 * </MagneticButton>
 * ```
 */

import { useRef, type ReactNode, type MouseEvent } from 'react';
import { m, useMotionValue, useSpring } from 'framer-motion';
import { SOFT_SPRING } from '../../utils/motion';

interface MagneticButtonProps {
  children: ReactNode;
  /** 吸附强度（0~1），0 = 无吸附，1 = 完全跟手，默认 0.3 */
  strength?: number;
  className?: string;
}

export function MagneticButton({
  children,
  strength = 0.3,
  className,
}: MagneticButtonProps) {
  const ref = useRef<HTMLDivElement>(null);

  // motionValue 直接驱动 transform，不触发 React rerender
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  // 用 spring 包裹一层让回弹更自然
  const sx = useSpring(x, SOFT_SPRING);
  const sy = useSpring(y, SOFT_SPRING);

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    x.set((e.clientX - cx) * strength);
    y.set((e.clientY - cy) * strength);
  };

  const handleMouseLeave = () => {
    x.set(0);
    y.set(0);
  };

  return (
    <m.div
      ref={ref}
      className={className}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{ x: sx, y: sy, display: 'inline-block' }}
    >
      {children}
    </m.div>
  );
}
