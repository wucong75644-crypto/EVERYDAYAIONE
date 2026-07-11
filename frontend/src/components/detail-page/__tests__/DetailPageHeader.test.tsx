import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DetailPageHeader } from '../DetailPageHeader';

const navigate = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: (selector: (state: { user: { nickname: string; credits: number } }) => unknown) =>
    selector({ user: { nickname: '测试用户', credits: 872 } }),
}));

describe('DetailPageHeader', () => {
  beforeEach(() => {
    navigate.mockClear();
  });

  it('显示页面名称、积分和用户头像', () => {
    render(<DetailPageHeader />);
    expect(screen.getByText('主图&详情图')).toBeInTheDocument();
    expect(screen.getByText('872')).toBeInTheDocument();
    expect(screen.getByLabelText('测试用户')).toHaveTextContent('测');
  });

  it('无浏览历史时点击返回进入聊天页', () => {
    render(<DetailPageHeader />);
    fireEvent.click(screen.getByRole('button', { name: '返回聊天' }));
    expect(navigate).toHaveBeenCalledWith('/chat');
  });

  it('有浏览历史时点击返回执行后退', () => {
    Object.defineProperty(window.history, 'length', { configurable: true, value: 2 });
    render(<DetailPageHeader />);
    fireEvent.click(screen.getByRole('button', { name: '返回聊天' }));
    expect(navigate).toHaveBeenCalledWith(-1);
  });
});
