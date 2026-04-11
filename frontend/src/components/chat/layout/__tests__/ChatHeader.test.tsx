/**
 * ChatHeader 测试
 *
 * 重点覆盖 V3 Phase 4 新加的搜索按钮（onOpenSearch prop），
 * 以及原有的标题编辑 / 侧边栏切换 / 积分显示等核心行为。
 */

import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ChatHeader } from '../ChatHeader';

// ChatHeader 内部用 useNavigate（组织管理入口），所有渲染都要 Router 包裹
const render: typeof rtlRender = (ui, options) =>
  rtlRender(<MemoryRouter>{ui}</MemoryRouter>, options);

const baseProps = {
  sidebarCollapsed: false,
  onToggleSidebar: vi.fn(),
  conversationTitle: '测试对话',
  isEditingTitle: false,
  editingTitle: '',
  onEditingTitleChange: vi.fn(),
  onTitleDoubleClick: vi.fn(),
  onTitleSubmit: vi.fn(),
  onTitleCancel: vi.fn(),
  userCredits: 100,
};

describe('ChatHeader — 基础渲染', () => {
  it('渲染对话标题', () => {
    render(<ChatHeader {...baseProps} />);
    expect(screen.getByText('测试对话')).toBeInTheDocument();
  });

  it('渲染用户积分', () => {
    render(<ChatHeader {...baseProps} userCredits={250} />);
    expect(screen.getByText('250')).toBeInTheDocument();
  });
});

describe('ChatHeader — 搜索按钮（V3 Phase 4）', () => {
  it('未传 onOpenSearch 时不渲染搜索按钮', () => {
    render(<ChatHeader {...baseProps} />);
    expect(screen.queryByLabelText('搜索消息')).not.toBeInTheDocument();
  });

  it('传了 onOpenSearch 时渲染搜索按钮', () => {
    render(<ChatHeader {...baseProps} onOpenSearch={vi.fn()} />);
    expect(screen.getByLabelText('搜索消息')).toBeInTheDocument();
  });

  it('点击搜索按钮触发 onOpenSearch 回调', () => {
    const onOpenSearch = vi.fn();
    render(<ChatHeader {...baseProps} onOpenSearch={onOpenSearch} />);
    fireEvent.click(screen.getByLabelText('搜索消息'));
    expect(onOpenSearch).toHaveBeenCalledTimes(1);
  });

  it('搜索按钮 title 包含 Cmd+F 提示', () => {
    render(<ChatHeader {...baseProps} onOpenSearch={vi.fn()} />);
    const btn = screen.getByLabelText('搜索消息');
    expect(btn).toHaveAttribute('title', expect.stringContaining('Cmd+F'));
  });
});

describe('ChatHeader — 标题编辑', () => {
  it('双击标题触发 onTitleDoubleClick', () => {
    const onTitleDoubleClick = vi.fn();
    render(<ChatHeader {...baseProps} onTitleDoubleClick={onTitleDoubleClick} />);
    fireEvent.doubleClick(screen.getByText('测试对话'));
    expect(onTitleDoubleClick).toHaveBeenCalled();
  });

  it('isEditingTitle=true 时渲染 input 而不是 h1', () => {
    render(
      <ChatHeader
        {...baseProps}
        isEditingTitle={true}
        editingTitle="新标题"
      />,
    );
    expect(screen.getByDisplayValue('新标题')).toBeInTheDocument();
  });

  it('编辑模式下按 Enter 触发 onTitleSubmit', () => {
    const onTitleSubmit = vi.fn();
    render(
      <ChatHeader
        {...baseProps}
        isEditingTitle={true}
        editingTitle="新标题"
        onTitleSubmit={onTitleSubmit}
      />,
    );
    fireEvent.keyDown(screen.getByDisplayValue('新标题'), { key: 'Enter' });
    expect(onTitleSubmit).toHaveBeenCalled();
  });

  it('编辑模式下按 Escape 触发 onTitleCancel', () => {
    const onTitleCancel = vi.fn();
    render(
      <ChatHeader
        {...baseProps}
        isEditingTitle={true}
        editingTitle="新标题"
        onTitleCancel={onTitleCancel}
      />,
    );
    fireEvent.keyDown(screen.getByDisplayValue('新标题'), { key: 'Escape' });
    expect(onTitleCancel).toHaveBeenCalled();
  });
});

describe('ChatHeader — 侧边栏切换', () => {
  it('sidebarCollapsed=true 时显示展开按钮', () => {
    render(<ChatHeader {...baseProps} sidebarCollapsed={true} />);
    expect(screen.getByTitle('展开侧边栏')).toBeInTheDocument();
  });

  it('sidebarCollapsed=false 时不显示展开按钮', () => {
    render(<ChatHeader {...baseProps} sidebarCollapsed={false} />);
    expect(screen.queryByTitle('展开侧边栏')).not.toBeInTheDocument();
  });

  it('点击展开按钮触发 onToggleSidebar', () => {
    const onToggleSidebar = vi.fn();
    render(
      <ChatHeader
        {...baseProps}
        sidebarCollapsed={true}
        onToggleSidebar={onToggleSidebar}
      />,
    );
    fireEvent.click(screen.getByTitle('展开侧边栏'));
    expect(onToggleSidebar).toHaveBeenCalled();
  });
});
