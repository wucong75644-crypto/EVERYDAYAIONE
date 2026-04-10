/**
 * Dropdown 组件测试
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { Dropdown, DropdownItem, DropdownDivider } from '../Dropdown';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('Dropdown', () => {
  it('初始状态菜单不渲染', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>项目1</DropdownItem>
      </Dropdown>,
    );
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.queryByText('项目1')).not.toBeInTheDocument();
  });

  it('点击触发器展开菜单', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>项目1</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(screen.getByText('项目1')).toBeInTheDocument();
  });

  it('再次点击触发器收起菜单（动画结束后卸载）', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>项目1</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    fireEvent.click(screen.getByText('菜单'));
    // 动画播放中菜单仍存在
    expect(screen.getByRole('menu')).toBeInTheDocument();

    // 动画结束后卸载
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('点击外部关闭菜单', () => {
    render(
      <div>
        <Dropdown trigger={<button>菜单</button>}>
          <DropdownItem>项目1</DropdownItem>
        </Dropdown>
        <div data-testid="outside">外部</div>
      </div>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId('outside'));
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('ESC 键关闭菜单', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>项目1</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('placement bottom 默认位于下方', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>x</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu').className).toContain('top-full');
  });

  it('placement top 位于上方', () => {
    render(
      <Dropdown trigger={<button>菜单</button>} placement="top">
        <DropdownItem>x</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu').className).toContain('bottom-full');
  });

  it('align end 右对齐', () => {
    render(
      <Dropdown trigger={<button>菜单</button>} align="end">
        <DropdownItem>x</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('menu').className).toContain('right-0');
  });
});

describe('DropdownItem', () => {
  it('点击触发 onClick', () => {
    const handleClick = vi.fn();
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem onClick={handleClick}>项目</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    fireEvent.click(screen.getByText('项目'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('disabled 状态不可点击', () => {
    const handleClick = vi.fn();
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem onClick={handleClick} disabled>
          禁用项
        </DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    fireEvent.click(screen.getByText('禁用项'));
    expect(handleClick).not.toHaveBeenCalled();
  });

  it('danger variant 使用 error 颜色', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem variant="danger">删除</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    const item = screen.getByRole('menuitem');
    expect(item.className).toContain('text-error');
  });

  it('支持 icon 和 trailing', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem
          icon={<span data-testid="icon">★</span>}
          trailing={<span data-testid="trailing">▶</span>}
        >
          项目
        </DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByTestId('trailing')).toBeInTheDocument();
  });
});

describe('DropdownDivider', () => {
  it('渲染分隔线', () => {
    render(
      <Dropdown trigger={<button>菜单</button>}>
        <DropdownItem>1</DropdownItem>
        <DropdownDivider />
        <DropdownItem>2</DropdownItem>
      </Dropdown>,
    );
    fireEvent.click(screen.getByText('菜单'));
    expect(screen.getByRole('separator')).toBeInTheDocument();
  });
});
