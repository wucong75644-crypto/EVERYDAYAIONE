/**
 * 主题切换 Hook
 *
 * 管理两个独立维度：
 * - theme: 主题风格（'classic' 经典蓝 / 'claude' 暖色文学 / 'linear' 暗色工程）
 * - colorMode: 明暗模式（'light' / 'dark' / 'system' 跟随系统）
 *
 * 工作机制：
 * - 主题风格通过 <html data-theme="..."> 控制
 * - 明暗模式通过 <html class="dark|light"> 控制
 * - 切换时给 html 临时加 .theme-transitioning class 实现 300ms 平滑过渡
 * - 状态持久化到 localStorage
 *
 * 防闪白：初始化由 index.html 的同步脚本完成（在 React hydrate 之前）
 *
 * @example
 * ```tsx
 * const { theme, setTheme, colorMode, setColorMode, isDark } = useTheme();
 * setTheme('linear');     // 切换到 Linear 暗色工程主题
 * setColorMode('dark');   // 切换到深色模式
 * ```
 */

import { useState, useEffect, useCallback } from 'react';

export type ThemeName = 'classic' | 'claude' | 'linear';
const VALID_THEMES: readonly ThemeName[] = ['classic', 'claude', 'linear'] as const;
export type ColorMode = 'light' | 'dark' | 'system';

const THEME_KEY = 'everydayai_theme';
const COLOR_MODE_KEY = 'everydayai_color_mode';
const TRANSITION_CLASS = 'theme-transitioning';
const TRANSITION_DURATION = 350; // 与 --duration-slower 一致

/**
 * 计算 colorMode 在当前环境下实际生效的明暗
 */
function resolveIsDark(colorMode: ColorMode): boolean {
  if (colorMode === 'dark') return true;
  if (colorMode === 'light') return false;
  // system: 跟随系统偏好
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * 临时启用全局主题过渡动画
 * 切换主题时调用，过渡完成后自动移除
 */
function enableThemeTransition(): void {
  const html = document.documentElement;
  html.classList.add(TRANSITION_CLASS);
  window.setTimeout(() => {
    html.classList.remove(TRANSITION_CLASS);
  }, TRANSITION_DURATION);
}

/**
 * 应用主题到 html 元素
 */
function applyTheme(theme: ThemeName, isDark: boolean): void {
  const html = document.documentElement;
  html.setAttribute('data-theme', theme);
  if (isDark) {
    html.classList.add('dark');
    html.classList.remove('light');
  } else {
    html.classList.add('light');
    html.classList.remove('dark');
  }
}

/**
 * 从 localStorage 读取初始主题（带容错）
 */
function getInitialTheme(): ThemeName {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored && (VALID_THEMES as readonly string[]).includes(stored)) {
      return stored as ThemeName;
    }
  } catch {
    // localStorage 不可用
  }
  return 'classic';
}

/**
 * 从 localStorage 读取初始明暗模式（带容错）
 */
function getInitialColorMode(): ColorMode {
  try {
    const stored = localStorage.getItem(COLOR_MODE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // localStorage 不可用
  }
  return 'system';
}

interface UseThemeReturn {
  /** 当前主题风格 */
  theme: ThemeName;
  /** 当前明暗模式（用户的选择） */
  colorMode: ColorMode;
  /** 实际生效的明暗（system 模式会被解析为 light/dark） */
  isDark: boolean;
  /** 切换主题风格 */
  setTheme: (theme: ThemeName) => void;
  /** 切换明暗模式 */
  setColorMode: (mode: ColorMode) => void;
}

export function useTheme(): UseThemeReturn {
  const [theme, setThemeState] = useState<ThemeName>(getInitialTheme);
  const [colorMode, setColorModeState] = useState<ColorMode>(getInitialColorMode);
  const [isDark, setIsDark] = useState<boolean>(() => resolveIsDark(getInitialColorMode()));

  // 监听系统明暗变化（仅在 colorMode === 'system' 时生效）
  useEffect(() => {
    if (colorMode !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = (e: MediaQueryListEvent) => {
      setIsDark(e.matches);
      applyTheme(theme, e.matches);
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [colorMode, theme]);

  const setTheme = useCallback((next: ThemeName) => {
    enableThemeTransition();
    setThemeState(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // localStorage 不可用，状态仅在内存中
    }
    applyTheme(next, isDark);
  }, [isDark]);

  const setColorMode = useCallback((mode: ColorMode) => {
    enableThemeTransition();
    const nextIsDark = resolveIsDark(mode);
    setColorModeState(mode);
    setIsDark(nextIsDark);
    try {
      localStorage.setItem(COLOR_MODE_KEY, mode);
    } catch {
      // localStorage 不可用
    }
    applyTheme(theme, nextIsDark);
  }, [theme]);

  return { theme, colorMode, isDark, setTheme, setColorMode };
}
