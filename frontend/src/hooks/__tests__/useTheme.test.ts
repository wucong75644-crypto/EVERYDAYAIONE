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

describe('useTheme - Linear 主题（V3 Phase 2 新增）', () => {
  it('localStorage 中持久化的 linear 主题能正确读取', () => {
    localStorage.setItem('everydayai_theme', 'linear');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('linear');
  });

  it('setTheme("linear") 后 DOM data-theme 属性更新', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('linear'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('linear');
  });

  it('linear + colorMode=system → 强制 isDark=true（DESIGN.md 要求 dark-first）', () => {
    // 即使系统偏好是 light（默认 matchMedia.matches=false），Linear 也应该 dark
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('linear'));
    // 切到 linear 后虽然 colorMode 还是 'system'（默认），但 isDark 应该强制 true
    expect(result.current.colorMode).toBe('system');
    expect(result.current.isDark).toBe(true);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('classic + colorMode=system 回归保护：仍跟随系统偏好（非 Linear 不受影响）', () => {
    // 默认 matchMedia.matches=false → classic system 应该是 light
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('classic');
    expect(result.current.colorMode).toBe('system');
    expect(result.current.isDark).toBe(false);
  });

  it('linear + colorMode=light：用户显式选 light 时尊重用户选择', () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme('linear'));
    act(() => result.current.setColorMode('light'));
    expect(result.current.isDark).toBe(false);
    expect(document.documentElement.classList.contains('light')).toBe(true);
  });

  it('从 classic 切到 linear 时 isDark 联动更新（不需要手动改 colorMode）', () => {
    const { result } = renderHook(() => useTheme());
    // 初始：classic + system → isDark=false
    expect(result.current.isDark).toBe(false);
    // 切到 linear：isDark 自动变 true
    act(() => result.current.setTheme('linear'));
    expect(result.current.isDark).toBe(true);
    // 切回 classic：isDark 恢复跟随系统偏好（false）
    act(() => result.current.setTheme('classic'));
    expect(result.current.isDark).toBe(false);
  });

  it('ThemeName 白名单验证：无效值回退到 classic（包括旧版不存在的 linear 命中白名单）', () => {
    localStorage.setItem('everydayai_theme', 'random-invalid');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('classic');
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
