/**
 * Reveal — 滚动显现组件
 *
 * 子元素进入视口时 spring fade-up，离开视口后不重播（只触发一次）。
 * 首页长页面的每个 section 用这个包裹即可自动获得"滚动就有东西冒出来"的效果。
 *
 * @example
 * ```tsx
 * <Reveal>
 *   <h2>一个标题</h2>
 * </Reveal>
 *
 * <Reveal delay={0.1} y={24}>
 *   <p>稍晚一点进场 + 更大位移</p>
 * </Reveal>
 * ```
 */

import { useRef, type ReactNode } from 'react';
import { m, useInView } from 'framer-motion';
import { SOFT_SPRING } from '../../utils/motion';

interface RevealProps {
  children: ReactNode;
  /** 初始位移 y（px），默认 16 */
  y?: number;
  /** 延迟（秒） */
  delay?: number;
  /** 触发阈值（0~1），默认 0.15 */
  amount?: number;
  /** 是否只触发一次（默认 true） */
  once?: boolean;
  className?: string;
}

export function Reveal({
  children,
  y = 16,
  delay = 0,
  amount = 0.15,
  once = true,
  className,
}: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once, amount });

  return (
    <m.div
      ref={ref}
      className={className}
      initial={{ opacity: 0, y }}
      animate={inView ? { opacity: 1, y: 0 } : { opacity: 0, y }}
      transition={{ ...SOFT_SPRING, delay }}
    >
      {children}
    </m.div>
  );
}
