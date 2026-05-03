/**
 * FileMentionDropdown 组件单测
 *
 * 覆盖：
 * - 空状态提示
 * - loading 状态
 * - 结果列表渲染
 * - 高亮项样式
 * - 点击选中回调
 * - 鼠标悬停回调
 * - 子目录路径显示
 */

import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import FileMentionDropdown from '../FileMentionDropdown';

// jsdom 不实现 scrollIntoView
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
import type { MentionResult } from '../../../../hooks/useFileMention';

const mockFile: MentionResult = {
  name: 'report.xlsx',
  workspace_path: 'report.xlsx',
  cdn_url: 'https://cdn.test/report.xlsx',
  mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  size: 1024,
};

const mockSubdirFile: MentionResult = {
  name: 'data.csv',
  workspace_path: 'exports/data.csv',
  cdn_url: 'https://cdn.test/exports/data.csv',
  mime_type: 'text/csv',
  size: 512,
};

describe('FileMentionDropdown', () => {
  it('should render nothing when empty and not loading', () => {
    const { container } = render(
      <FileMentionDropdown
        results={[]}
        activeIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('should render loading state', () => {
    render(
      <FileMentionDropdown
        results={[]}
        activeIndex={0}
        loading={true}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    expect(screen.getByText('搜索中...')).toBeTruthy();
  });

  it('should render file results', () => {
    render(
      <FileMentionDropdown
        results={[mockFile, mockSubdirFile]}
        activeIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    expect(screen.getByText('report.xlsx')).toBeTruthy();
    expect(screen.getByText('data.csv')).toBeTruthy();
  });

  it('should show subdirectory path for nested files', () => {
    render(
      <FileMentionDropdown
        results={[mockSubdirFile]}
        activeIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    // workspace_path !== name 时显示路径
    expect(screen.getByText('exports/data.csv')).toBeTruthy();
  });

  it('should not show path for root-level files', () => {
    render(
      <FileMentionDropdown
        results={[mockFile]}
        activeIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    // workspace_path === name，不显示额外路径
    const pathElements = screen.queryAllByText('report.xlsx');
    // 只有一个（文件名），没有路径
    expect(pathElements).toHaveLength(1);
  });

  it('should call onSelect on mouseDown', () => {
    const onSelect = vi.fn();
    render(
      <FileMentionDropdown
        results={[mockFile]}
        activeIndex={0}
        loading={false}
        onSelect={onSelect}
        onHover={vi.fn()}
      />,
    );

    const button = screen.getByText('report.xlsx').closest('button')!;
    fireEvent.mouseDown(button);
    expect(onSelect).toHaveBeenCalledWith(mockFile);
  });

  it('should call onHover on mouseEnter', () => {
    const onHover = vi.fn();
    render(
      <FileMentionDropdown
        results={[mockFile, mockSubdirFile]}
        activeIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onHover={onHover}
      />,
    );

    const secondItem = screen.getByText('data.csv').closest('button')!;
    fireEvent.mouseEnter(secondItem);
    expect(onHover).toHaveBeenCalledWith(1);
  });

  it('should show results even when loading (incremental)', () => {
    render(
      <FileMentionDropdown
        results={[mockFile]}
        activeIndex={0}
        loading={true}
        onSelect={vi.fn()}
        onHover={vi.fn()}
      />,
    );
    // loading=true 但有结果时，显示结果而非 loading 提示
    expect(screen.getByText('report.xlsx')).toBeTruthy();
    expect(screen.queryByText('搜索中...')).toBeNull();
  });
});
