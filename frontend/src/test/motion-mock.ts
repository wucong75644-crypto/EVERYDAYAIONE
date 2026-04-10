/**
 * Framer Motion jsdom mock
 *
 * jsdom 不支持 framer-motion 需要的 Web Animations API 完整实现，
 * 同时动画在测试里没有验证价值（跑不完就 cleanup），会制造假阳性警告。
 *
 * 策略：通过 `MotionGlobalConfig.skipAnimations = true` 关闭所有动画，
 * motion.* 组件仍会渲染正确的 DOM，只是直接跳到 animate 终态。
 * 这是 framer-motion 官方推荐的测试方式。
 *
 * 同时 mock IntersectionObserver / ResizeObserver / matchMedia 等
 * framer 在某些 hook 里依赖的 API。
 *
 * 被 src/test/setup.ts 引入，全局生效。
 */

import { vi } from 'vitest';
import { MotionGlobalConfig } from 'framer-motion';

/* ============================================================
 * 1. 跳过所有动画（核心开关）
 * ============================================================ */

MotionGlobalConfig.skipAnimations = true;

/* ============================================================
 * 2. IntersectionObserver（framer useInView / Reveal 依赖）
 * ============================================================ */

if (typeof window !== 'undefined' && !('IntersectionObserver' in window)) {
  class MockIntersectionObserver {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = vi.fn().mockReturnValue([]);
    root = null;
    rootMargin = '';
    thresholds = [];
  }

  // @ts-expect-error — jsdom polyfill
  window.IntersectionObserver = MockIntersectionObserver;
  // @ts-expect-error — jsdom polyfill
  global.IntersectionObserver = MockIntersectionObserver;
}

/* ============================================================
 * 3. ResizeObserver（framer layout 动画依赖）
 * ============================================================ */

if (typeof window !== 'undefined' && !('ResizeObserver' in window)) {
  class MockResizeObserver {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  }

  // @ts-expect-error — jsdom polyfill
  window.ResizeObserver = MockResizeObserver;
  // @ts-expect-error — jsdom polyfill
  global.ResizeObserver = MockResizeObserver;
}

/* ============================================================
 * 4. scrollTo（某些 framer hook 调用）
 * ============================================================ */

if (typeof window !== 'undefined' && !window.scrollTo) {
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
}
