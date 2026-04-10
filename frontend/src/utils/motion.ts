/**
 * Framer Motion 工具库
 *
 * 集中管理：
 * 1. Spring preset — 所有动效共享的物理参数，确保一致的"手感"
 * 2. Variants preset — 常用进出场 variants（fade/slide/scale/stagger）
 * 3. Reduced motion — 统一读取用户的 prefers-reduced-motion 偏好
 *
 * 使用 LazyMotion + domAnimation 做 tree-shaking，
 * main.tsx 已经包了 <LazyMotion features={domAnimation}>，
 * 所以业务组件请用 `motion.div` 的 `m` 别名或直接 `motion.div`。
 *
 * 设计原则（苹果 HIG）：
 * - 进入慢、退出快（hover/tap 除外）
 * - 只动 transform + opacity（GPU）
 * - spring damping 高于默认值（防止过度回弹显得廉价）
 */

import type { Transition, Variants } from 'framer-motion';

/* ============================================================
 * Spring Presets
 * ============================================================ */

/** 柔软回弹 — Button hover / Card hover / 大部分 UI 反馈 */
export const SOFT_SPRING: Transition = {
  type: 'spring',
  stiffness: 400,
  damping: 30,
  mass: 0.8,
};

/** 紧致回弹 — Dropdown / Popover / 快速反馈 */
export const SNAPPY_SPRING: Transition = {
  type: 'spring',
  stiffness: 500,
  damping: 35,
  mass: 0.6,
};

/** 弹跳回弹 — Toast / 强调入场 */
export const BOUNCY_SPRING: Transition = {
  type: 'spring',
  stiffness: 300,
  damping: 20,
  mass: 1,
};

/** 液体回弹 — Modal / Drawer / 大幅度位移 */
export const FLUID_SPRING: Transition = {
  type: 'spring',
  stiffness: 260,
  damping: 28,
  mass: 1,
};

/* ============================================================
 * Duration-based easings（非 spring 时使用）
 * ============================================================ */

/** 苹果 spring 近似曲线，用在纯时长驱动的过渡 */
export const APPLE_EASE: Transition = {
  duration: 0.25,
  ease: [0.32, 0.72, 0, 1],
};

/** 退出专用（更快） */
export const EXIT_EASE: Transition = {
  duration: 0.15,
  ease: [0.55, 0.06, 0.68, 0.19],
};

/* ============================================================
 * Variants Presets — 常用进出场
 * ============================================================ */

/** 淡入淡出 */
export const fadeVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: APPLE_EASE },
  exit: { opacity: 0, transition: EXIT_EASE },
};

/** 上滑进入（从下往上 12px） */
export const slideUpVariants: Variants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: SOFT_SPRING },
  exit: { opacity: 0, y: 8, transition: EXIT_EASE },
};

/** 下滑进入（Dropdown / Menu 从上往下 6px） */
export const slideDownVariants: Variants = {
  initial: { opacity: 0, y: -6, scale: 0.97 },
  animate: { opacity: 1, y: 0, scale: 1, transition: SNAPPY_SPRING },
  exit: { opacity: 0, y: -6, scale: 0.97, transition: EXIT_EASE },
};

/** 缩放淡入（Modal） */
export const scaleVariants: Variants = {
  initial: { opacity: 0, scale: 0.95, y: 10 },
  animate: { opacity: 1, scale: 1, y: 0, transition: FLUID_SPRING },
  exit: { opacity: 0, scale: 0.95, y: 10, transition: EXIT_EASE },
};

/** 右侧滑入（Drawer） */
export const slideRightVariants: Variants = {
  initial: { x: '100%' },
  animate: { x: 0, transition: FLUID_SPRING },
  exit: { x: '100%', transition: EXIT_EASE },
};

/** Stagger 容器（子元素依次入场） */
export const staggerContainer: Variants = {
  animate: {
    transition: {
      staggerChildren: 0.04,
      delayChildren: 0.02,
    },
  },
};

/** Stagger 子元素 */
export const staggerItem: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: SOFT_SPRING },
};

/* ============================================================
 * Gesture Presets — whileHover / whileTap
 * ============================================================ */

/** 按钮：悬浮微浮起 + 按下微缩 */
export const buttonHoverTap = {
  whileHover: { y: -1, scale: 1.02 },
  whileTap: { scale: 0.96, y: 0 },
  transition: SOFT_SPRING,
} as const;

/** 卡片：悬浮抬升 */
export const cardHover = {
  whileHover: { y: -2 },
  transition: SOFT_SPRING,
} as const;

/** Icon 按钮：旋转或微缩 */
export const iconButtonHoverTap = {
  whileHover: { scale: 1.08 },
  whileTap: { scale: 0.9 },
  transition: SNAPPY_SPRING,
} as const;
