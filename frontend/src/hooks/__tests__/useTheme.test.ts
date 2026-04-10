/**
 * useTheme Hook 测试
 *
 * 覆盖：
 * - 默认主题为 classic + system
 * - localStorage 持久化
 * - setTheme 切换主题风格 + DOM 更新
 * - setColorMode 切换明暗模式 + DOM 更新
 * - system 模式跟随 prefers-color-scheme
 * - localStorage 不可用时容错
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTheme } from '../useTheme';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.classList.remove('dark', 'light', 'theme-transitioning');

  // 默认 prefers-color-scheme: light（设置在 setup.ts 中 matches=false）
  vi.mocked(window.matchMedia).mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

describe('useTheme - 初始状态', () => {
  it('默认主题为 classic', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('classic');
  });

  it('默认明暗模式为 system', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.colorMode).toBe('system');
  });

  it('system 模式下 isDark 跟随 matchMedia 结果', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.isDark).toBe(false);
  });

  it('系统偏好为 dark 时 isDark 为 true', () => {
    vi.mocked(window.matchMedia).mockImplementation((query) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { result } = renderHook(() => useTheme());
    expect(result.current.isDark).toBe(true);
  });
});

describe('useTheme - localStorage 持久化', () => {
  it('从 localStorage 读取已保存的主题', () => {
    localStorage.setItem('everydayai_theme', 'claude');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('claude');
  });

  it('从 localStorage 读取已保存的明暗模式', () => {
    localStorage.setItem('everydayai_color_mode', 'dark');
    const { result } = renderHook(() => useTheme());
    expect(result.current.colorMode).toBe('dark');
    expect(result.current.isDark).toBe(true);
  });

  it('localStorage 中无效值回退到默认', () => {
    localStorage.setItem('everydayai_theme', 'invalid-theme');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('classic');
  });
});

describe('useTheme - setTheme 切换主题风格', () => {
  it('调用 setTheme 后 state 更新', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('claude'));
    expect(result.current.theme).toBe('claude');
  });

  it('调用 setTheme 后 html 元素 data-theme 属性更新', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('claude'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('claude');
  });

  it('调用 setTheme 后写入 localStorage', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('claude'));
    expect(localStorage.getItem('everydayai_theme')).toBe('claude');
  });

  it('切换主题时临时启用 theme-transitioning class', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('claude'));
    expect(document.documentElement.classList.contains('theme-transitioning')).toBe(true);
  });
});

describe('useTheme - setColorMode 切换明暗模式', () => {
  it('调用 setColorMode("dark") 后 isDark 为 true', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setColorMode('dark'));
    expect(result.current.isDark).toBe(true);
    expect(result.current.colorMode).toBe('dark');
  });

  it('切换到 dark 后 html 添加 dark class', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setColorMode('dark'));
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.classList.contains('light')).toBe(false);
  });

  it('切换到 light 后 html 添加 light class', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setColorMode('light'));
    expect(document.documentElement.classList.contains('light')).toBe(true);
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('调用 setColorMode 后写入 localStorage', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setColorMode('dark'));
    expect(localStorage.getItem('everydayai_color_mode')).toBe('dark');
  });
});

describe('useTheme - 容错', () => {
  it('localStorage 不可用时仍能正常初始化', () => {
    const originalGetItem = Storage.prototype.getItem;
    Storage.prototype.getItem = vi.fn(() => {
      throw new Error('localStorage disabled');
    });

    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('classic');
    expect(result.current.colorMode).toBe('system');

    Storage.prototype.getItem = originalGetItem;
  });

  it('localStorage 写入失败时不抛出错误', () => {
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = vi.fn(() => {
      throw new Error('quota exceeded');
    });

    const { result } = renderHook(() => useTheme());
    expect(() => {
      act(() => result.current.setTheme('claude'));
    }).not.toThrow();
    expect(result.current.theme).toBe('claude');

    Storage.prototype.setItem = originalSetItem;
  });
});
