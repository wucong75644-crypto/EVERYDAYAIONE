/**
 * useFileSelection Hook 单测
 *
 * 覆盖：单选、Ctrl 多选、Shift 范围选、全选、清空、handleClick 路由。
 */

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFileSelection } from '../useFileSelection';

const PATHS = ['a.txt', 'b.txt', 'c.txt', 'd.txt', 'e.txt'];

describe('useFileSelection', () => {
  it('should initialize with empty selection', () => {
    const { result } = renderHook(() => useFileSelection());
    expect(result.current.selectedCount).toBe(0);
    expect(result.current.hasSelection).toBe(false);
  });

  describe('select (single click)', () => {
    it('should select a single file', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('a.txt'));
      expect(result.current.selectedPaths.has('a.txt')).toBe(true);
      expect(result.current.selectedCount).toBe(1);
    });

    it('should replace previous selection', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('a.txt'));
      act(() => result.current.select('b.txt'));
      expect(result.current.selectedPaths.has('a.txt')).toBe(false);
      expect(result.current.selectedPaths.has('b.txt')).toBe(true);
      expect(result.current.selectedCount).toBe(1);
    });
  });

  describe('toggle (Ctrl+click)', () => {
    it('should add to selection', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('a.txt'));
      act(() => result.current.toggle('b.txt'));
      expect(result.current.selectedCount).toBe(2);
      expect(result.current.selectedPaths.has('a.txt')).toBe(true);
      expect(result.current.selectedPaths.has('b.txt')).toBe(true);
    });

    it('should remove if already selected', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('a.txt'));
      act(() => result.current.toggle('a.txt'));
      expect(result.current.selectedCount).toBe(0);
    });
  });

  describe('selectRange (Shift+click)', () => {
    it('should select range from anchor to target', () => {
      const { result } = renderHook(() => useFileSelection());
      // 先单击 b.txt 设锚点
      act(() => result.current.select('b.txt'));
      // Shift 选 d.txt → 选中 b,c,d
      act(() => result.current.selectRange('d.txt', PATHS));
      expect(result.current.selectedCount).toBe(3);
      expect(result.current.selectedPaths.has('b.txt')).toBe(true);
      expect(result.current.selectedPaths.has('c.txt')).toBe(true);
      expect(result.current.selectedPaths.has('d.txt')).toBe(true);
    });

    it('should select range in reverse direction', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('d.txt'));
      act(() => result.current.selectRange('b.txt', PATHS));
      expect(result.current.selectedCount).toBe(3);
    });

    it('should fallback to single select without anchor', () => {
      const { result } = renderHook(() => useFileSelection());
      // 没有先单击，直接 Shift 选
      act(() => result.current.selectRange('c.txt', PATHS));
      expect(result.current.selectedCount).toBe(1);
      expect(result.current.selectedPaths.has('c.txt')).toBe(true);
    });

    it('should fallback if anchor not in list', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('z.txt')); // 不在 PATHS 中
      act(() => result.current.selectRange('c.txt', PATHS));
      expect(result.current.selectedCount).toBe(1);
      expect(result.current.selectedPaths.has('c.txt')).toBe(true);
    });
  });

  describe('selectAll', () => {
    it('should select all paths', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.selectAll(PATHS));
      expect(result.current.selectedCount).toBe(5);
    });
  });

  describe('clear', () => {
    it('should clear all selection', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.selectAll(PATHS));
      act(() => result.current.clear());
      expect(result.current.selectedCount).toBe(0);
      expect(result.current.hasSelection).toBe(false);
    });
  });

  describe('isSelected', () => {
    it('should return true for selected path', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.select('a.txt'));
      expect(result.current.isSelected('a.txt')).toBe(true);
      expect(result.current.isSelected('b.txt')).toBe(false);
    });
  });

  describe('handleClick', () => {
    it('should single select on plain click', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.handleClick('a.txt', PATHS, { ctrlKey: false, metaKey: false, shiftKey: false }));
      expect(result.current.selectedCount).toBe(1);
      expect(result.current.selectedPaths.has('a.txt')).toBe(true);
    });

    it('should toggle on Ctrl+click', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.handleClick('a.txt', PATHS, { ctrlKey: false, metaKey: false, shiftKey: false }));
      act(() => result.current.handleClick('b.txt', PATHS, { ctrlKey: true, metaKey: false, shiftKey: false }));
      expect(result.current.selectedCount).toBe(2);
    });

    it('should toggle on Cmd+click (macOS)', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.handleClick('a.txt', PATHS, { ctrlKey: false, metaKey: false, shiftKey: false }));
      act(() => result.current.handleClick('b.txt', PATHS, { ctrlKey: false, metaKey: true, shiftKey: false }));
      expect(result.current.selectedCount).toBe(2);
    });

    it('should range select on Shift+click', () => {
      const { result } = renderHook(() => useFileSelection());
      act(() => result.current.handleClick('b.txt', PATHS, { ctrlKey: false, metaKey: false, shiftKey: false }));
      act(() => result.current.handleClick('d.txt', PATHS, { ctrlKey: false, metaKey: false, shiftKey: true }));
      expect(result.current.selectedCount).toBe(3);
    });
  });
});
