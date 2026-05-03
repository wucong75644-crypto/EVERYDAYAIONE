/**
 * useFileMention Hook 单测
 *
 * 覆盖：
 * - extractMentionQuery 纯函数（@ 检测、边界条件）
 * - hook 初始状态
 * - consumeMention 精准替换
 * - handleKeyDown 键盘导航
 * - close 重置状态
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { extractMentionQuery, useFileMention } from '../useFileMention';

// Mock searchWorkspace — 避免真实 API 调用
vi.mock('../../services/workspace', () => ({
  searchWorkspace: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

// ============================================================
// extractMentionQuery 纯函数测试
// ============================================================

describe('extractMentionQuery', () => {
  it('should detect @ at start of text', () => {
    const result = extractMentionQuery('@hello', 6);
    expect(result).toEqual({ query: 'hello', start: 0 });
  });

  it('should detect @ after space', () => {
    const result = extractMentionQuery('say @file', 9);
    expect(result).toEqual({ query: 'file', start: 4 });
  });

  it('should return null when no @', () => {
    expect(extractMentionQuery('hello world', 11)).toBeNull();
  });

  it('should return null when @ is part of email', () => {
    // @ 前面不是空白
    expect(extractMentionQuery('user@test.com', 13)).toBeNull();
  });

  it('should return null when query contains space', () => {
    // "@hello world" — 空格出现说明 @ 已结束
    expect(extractMentionQuery('@hello world', 12)).toBeNull();
  });

  it('should return empty query when just @ typed', () => {
    const result = extractMentionQuery('@', 1);
    expect(result).toEqual({ query: '', start: 0 });
  });

  it('should use cursor position (not full text length)', () => {
    // 光标在 @he| 的位置，后面还有文字
    const result = extractMentionQuery('@hello world', 3);
    expect(result).toEqual({ query: 'he', start: 0 });
  });

  it('should find the last @ before cursor', () => {
    const result = extractMentionQuery('text @first done @second', 24);
    expect(result).toEqual({ query: 'second', start: 17 });
  });

  it('should handle @ after newline', () => {
    const result = extractMentionQuery('line1\n@file', 11);
    expect(result).toEqual({ query: 'file', start: 6 });
  });

  it('should handle multiple @ with cursor at first', () => {
    // 光标在第一个 @ 后面，第二个 @ 还没出现在光标范围
    const result = extractMentionQuery('@first @second', 6);
    expect(result).toEqual({ query: 'first', start: 0 });
  });
});

// ============================================================
// useFileMention hook 测试
// ============================================================

describe('useFileMention', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should initialize with closed state', () => {
    const { result } = renderHook(() => useFileMention());

    expect(result.current.showDropdown).toBe(false);
    expect(result.current.results).toEqual([]);
    expect(result.current.activeIndex).toBe(0);
    expect(result.current.loading).toBe(false);
  });

  describe('close', () => {
    it('should reset all state', () => {
      const { result } = renderHook(() => useFileMention());

      // 先打开
      act(() => result.current.handleInputChange('@test', 5));
      expect(result.current.showDropdown).toBe(true);

      // 关闭
      act(() => result.current.close());
      expect(result.current.showDropdown).toBe(false);
      expect(result.current.results).toEqual([]);
      expect(result.current.activeIndex).toBe(0);
    });
  });

  describe('handleInputChange', () => {
    it('should open dropdown when @ detected', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.handleInputChange('@', 1));
      expect(result.current.showDropdown).toBe(true);
    });

    it('should close dropdown when @ removed', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.handleInputChange('@test', 5));
      expect(result.current.showDropdown).toBe(true);

      act(() => result.current.handleInputChange('test', 4));
      expect(result.current.showDropdown).toBe(false);
    });

    it('should close when space typed after keyword', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.handleInputChange('@test ', 6));
      expect(result.current.showDropdown).toBe(false);
    });
  });

  describe('consumeMention', () => {
    it('should remove @keyword from prompt', () => {
      const { result } = renderHook(() => useFileMention());

      // 触发 @ 检测，记录 start 位置
      act(() => result.current.handleInputChange('@report', 7));

      let newPrompt = '';
      act(() => {
        newPrompt = result.current.consumeMention('@report');
      });
      expect(newPrompt).toBe('');
    });

    it('should preserve text before @', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.handleInputChange('hello @report', 13));

      let newPrompt = '';
      act(() => {
        newPrompt = result.current.consumeMention('hello @report');
      });
      expect(newPrompt).toBe('hello ');
    });

    it('should preserve text after @keyword', () => {
      const { result } = renderHook(() => useFileMention());

      // 模拟光标在 @report 后面，后面还有文字
      act(() => result.current.handleInputChange('see @report and more', 11));

      let newPrompt = '';
      act(() => {
        newPrompt = result.current.consumeMention('see @report and more');
      });
      expect(newPrompt).toBe('see  and more');
    });

    it('should handle precise @ position with multiple @', () => {
      const { result } = renderHook(() => useFileMention());

      // 光标在第二个 @ 后面
      act(() => result.current.handleInputChange('msg @file1 and @file2', 21));

      let newPrompt = '';
      act(() => {
        newPrompt = result.current.consumeMention('msg @file1 and @file2');
      });
      // 应该只移除第二个 @file2（因为 handleInputChange 记录的是最后一个 @ 的位置）
      expect(newPrompt).toBe('msg @file1 and ');
    });

    it('should return original prompt when no mention tracked', () => {
      const { result } = renderHook(() => useFileMention());

      let newPrompt = '';
      act(() => {
        newPrompt = result.current.consumeMention('hello world');
      });
      expect(newPrompt).toBe('hello world');
    });

    it('should close dropdown after consuming', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.handleInputChange('@test', 5));
      expect(result.current.showDropdown).toBe(true);

      act(() => result.current.consumeMention('@test'));
      expect(result.current.showDropdown).toBe(false);
    });
  });

  describe('handleKeyDown', () => {
    it('should return false when dropdown closed', () => {
      const { result } = renderHook(() => useFileMention());

      const event = { key: 'ArrowDown', preventDefault: vi.fn() } as unknown as React.KeyboardEvent;
      let handled = false;
      act(() => {
        handled = result.current.handleKeyDown(event);
      });
      expect(handled).toBe(false);
    });

    // 注：handleKeyDown 在 results 为空时返回 false（已在上面测试）。
    // Escape/ArrowDown/Enter 等在有结果时的行为需要先通过防抖搜索填充 results，
    // 这些场景在集成测试中验证更可靠。
  });

  describe('setActiveIndex', () => {
    it('should update active index', () => {
      const { result } = renderHook(() => useFileMention());

      act(() => result.current.setActiveIndex(3));
      expect(result.current.activeIndex).toBe(3);
    });
  });
});
