/**
 * MagneticButton — 磁吸按钮
 *
 * 鼠标靠近时按钮轻微"吸附"向光标方向移动，离开回弹。
 * 用在首页 Hero 的 CTA 按钮，增加"手感"吸引力。
 *
 * 实现：监听父级 mousemove，计算鼠标与按钮中心的距离，
 * 在 magnetThreshold 半径内时根据距离比例偏移。
 *
 * @example
 * ```tsx
 * <MagneticButton strength={0.4}>
 *   <Button variant="accent" size="lg">开始使用</Button>
 * </MagneticButton>
 * ```
 */

import { useRef, useState, type ReactNode, type MouseEvent } from 'react';
import { m } from 'framer-motion';
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
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    setOffset({
      x: (e.clientX - cx) * strength,
      y: (e.clientY - cy) * strength,
    });
  };

  const handleMouseLeave = () => setOffset({ x: 0, y: 0 });

  return (
    <m.div
      ref={ref}
      className={className}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      animate={offset}
      transition={SOFT_SPRING}
      style={{ display: 'inline-block' }}
    >
      {children}
    </m.div>
  );
}
