/**
 * PageTransition — 路由级页面过渡容器
 *
 * 每个路由页面最外层包一个 <PageTransition>，配合 App.tsx 的
 * <AnimatePresence mode="wait"> 实现路由切换时的 fade + slight slide 过渡。
 *
 * 对应 Phase 12 的路由升级。
 *
 * @example
 * ```tsx
 * // pages/Home.tsx
 * export default function Home() {
 *   return (
 *     <PageTransition>
 *       <div>首页内容</div>
 *     </PageTransition>
 *   );
 * }
 * ```
 *
 * @example
 * ```tsx
 * // App.tsx
 * <AnimatePresence mode="wait">
 *   <Routes location={location} key={location.pathname}>
 *     <Route path="/" element={<Home />} />
 *   </Routes>
 * </AnimatePresence>
 * ```
 */

import { type ReactNode } from 'react';
import { m } from 'framer-motion';
import { APPLE_EASE, EXIT_EASE } from '../../utils/motion';

interface PageTransitionProps {
  children: ReactNode;
  /** 初始位移 y（px），默认 8 */
  y?: number;
  className?: string;
}

export function PageTransition({
  children,
  y = 8,
  className,
}: PageTransitionProps) {
  return (
    <m.div
      className={className}
      initial={{ opacity: 0, y }}
      animate={{ opacity: 1, y: 0, transition: APPLE_EASE }}
      exit={{ opacity: 0, y: -y, transition: EXIT_EASE }}
    >
      {children}
    </m.div>
  );
}
