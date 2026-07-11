import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Sidebar from '../Sidebar';

vi.mock('../ConversationList', () => ({ default: () => <div>对话列表</div> }));
vi.mock('../../modals/SettingsModal', () => ({ default: () => null }));
vi.mock('../../modals/MemoryModal', () => ({ default: () => null }));
vi.mock('../../../../hooks/useClickOutside', () => ({ useClickOutside: vi.fn() }));
vi.mock('../../../../hooks/useLogout', () => ({ useLogout: () => vi.fn() }));
vi.mock('../../../../stores/useAuthStore', () => ({
  useAuthStore: () => ({ user: { nickname: '测试用户', role: 'user' }, currentOrg: null }),
}));
vi.mock('../../../../stores/useMessageStore', () => ({
  useMessageStore: { getState: () => ({ clearRecentlyCompleted: vi.fn() }) },
}));
vi.mock('../../../../stores/useMemoryStore', () => ({
  useMemoryStore: { getState: () => ({ openModal: vi.fn() }) },
}));

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>;
}

const baseProps = {
  collapsed: false,
  onToggle: vi.fn(),
  currentConversationId: null,
  onNewConversation: vi.fn(),
  onSelectConversation: vi.fn(),
  userCredits: 100,
};

describe('Sidebar 主图详情入口', () => {
  it('显示在 AI 记忆之前', () => {
    render(<MemoryRouter><Sidebar {...baseProps} /></MemoryRouter>);
    const labels = screen.getAllByRole('button').map((button) => button.textContent);
    expect(labels.indexOf('主图&详情图新')).toBeLessThan(labels.indexOf('AI 记忆'));
  });

  it('点击后跳转独立页面', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Sidebar {...baseProps} />
        <LocationProbe />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('主图&详情图'));
    expect(screen.getByTestId('location')).toHaveTextContent('/detail-page');
  });
});
