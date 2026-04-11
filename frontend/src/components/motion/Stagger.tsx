/**
 * Stagger — 子元素依次入场容器
 *
 * 把一个列表 / 卡片网格包起来，子元素按顺序 fade-up（默认每个延迟 40ms）。
 * 内部使用 framer-motion 的 staggerChildren + variants 机制。
 *
 * 子元素必须是 <StaggerItem>，否则不会应用动画。
 *
 * @example
 * ```tsx
 * <Stagger>
 *   {models.map(m => (
 *     <StaggerItem key={m.id}>
 *       <ModelCard {...m} />
 *     </StaggerItem>
 *   ))}
 * </Stagger>
 * ```
 */

import { type ReactNode } from 'react';
import { m } from 'framer-motion';
import { staggerContainer, staggerItem } from '../../utils/motion';

interface StaggerProps {
  children: ReactNode;
  /** 每个子元素的延迟间隔（秒），默认 0.04 */
  staggerDelay?: number;
  /** 首个子元素额外延迟（秒），默认 0.02 */
  initialDelay?: number;
  className?: string;
  /** 渲染为什么 tag（默认 div） */
  as?: 'div' | 'ul' | 'ol' | 'section';
}

export function Stagger({
  children,
  staggerDelay = 0.04,
  initialDelay = 0.02,
  className,
  as = 'div',
}: StaggerProps) {
  const variants = staggerDelay === 0.04 && initialDelay === 0.02
    ? staggerContainer
    : {
        initial: {},
        animate: {
          transition: {
            staggerChildren: staggerDelay,
            delayChildren: initialDelay,
          },
        },
      };

  // LazyMotion strict 模式下 m.div/m.ul/m.ol/m.section 都需单独调用
  if (as === 'ul') {
    return (
      <m.ul
        className={className}
        variants={variants}
        initial="initial"
        animate="animate"
      >
        {children}
      </m.ul>
    );
  }
  if (as === 'ol') {
    return (
      <m.ol
        className={className}
        variants={variants}
        initial="initial"
        animate="animate"
      >
        {children}
      </m.ol>
    );
  }
  if (as === 'section') {
    return (
      <m.section
        className={className}
        variants={variants}
        initial="initial"
        animate="animate"
      >
        {children}
      </m.section>
    );
  }
  return (
    <m.div
      className={className}
      variants={variants}
      initial="initial"
      animate="animate"
    >
      {children}
    </m.div>
  );
}

interface StaggerItemProps {
  children: ReactNode;
  className?: string;
}

export function StaggerItem({ children, className }: StaggerItemProps) {
  return (
    <m.div className={className} variants={staggerItem}>
      {children}
    </m.div>
  );
}
