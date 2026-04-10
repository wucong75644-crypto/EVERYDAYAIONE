/**
 * DropdownMenu primitive 测试
 *
 * 注意：Radix DropdownMenu.Trigger 使用 pointer events 而非 click 激活，
 * 所以不能用 fireEvent.click 开菜单。用受控 `open={true}` 绕过 trigger 交互
 * 测试菜单内容；trigger 的 pointer 交互属于 Radix 已测范围，留给 E2E。
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  DropdownMenu,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '../DropdownMenu';

describe('DropdownMenu', () => {
  it('初始时不显示菜单内容', () => {
    render(
      <DropdownMenu trigger={<button>open</button>}>
        <DropdownMenuItem>编辑</DropdownMenuItem>
      </DropdownMenu>,
    );
    expect(screen.queryByText('编辑')).not.toBeInTheDocument();
  });

  it('受控 open={true} 时显示菜单项（Portal 渲染）', () => {
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuItem>编辑</DropdownMenuItem>
        <DropdownMenuItem>删除</DropdownMenuItem>
      </DropdownMenu>,
    );
    expect(screen.getByText('编辑')).toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
  });

  it('DropdownMenuItem 接收 onSelect prop', () => {
    const handleEdit = vi.fn();
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuItem onSelect={handleEdit}>编辑</DropdownMenuItem>
      </DropdownMenu>,
    );
    expect(screen.getByText('编辑')).toBeInTheDocument();
  });

  it('disabled item 带 data-disabled 属性', () => {
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuItem disabled>禁用项</DropdownMenuItem>
      </DropdownMenu>,
    );
    const item = screen.getByText('禁用项').closest('[role="menuitem"]');
    expect(item).toHaveAttribute('data-disabled');
  });

  it('danger variant 仍渲染为 menuitem role', () => {
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuItem variant="danger">删除</DropdownMenuItem>
      </DropdownMenu>,
    );
    const item = screen.getByText('删除').closest('[role="menuitem"]');
    expect(item).toBeInTheDocument();
  });

  it('Separator 渲染', () => {
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuItem>一</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem>二</DropdownMenuItem>
      </DropdownMenu>,
    );
    expect(screen.getByRole('separator')).toBeInTheDocument();
  });

  it('Label 渲染', () => {
    render(
      <DropdownMenu trigger={<button>open</button>} open={true} onOpenChange={vi.fn()}>
        <DropdownMenuLabel>分组标题</DropdownMenuLabel>
        <DropdownMenuItem>项</DropdownMenuItem>
      </DropdownMenu>,
    );
    expect(screen.getByText('分组标题')).toBeInTheDocument();
  });

  it('非受控模式下 trigger 仍能渲染', () => {
    render(
      <DropdownMenu trigger={<button>click me</button>}>
        <DropdownMenuItem>菜单项</DropdownMenuItem>
      </DropdownMenu>,
    );
    // trigger 存在，menu 不显示
    expect(screen.getByText('click me')).toBeInTheDocument();
    expect(screen.queryByText('菜单项')).not.toBeInTheDocument();
  });
});
