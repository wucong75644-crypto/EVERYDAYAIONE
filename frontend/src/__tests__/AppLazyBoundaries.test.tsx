import { Suspense } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { DeferredAuthModal, ProtectedChatRoute } from '../App';
import { useAuthModalStore } from '../stores/useAuthModalStore';
import { useAuthStore } from '../stores/useAuthStore';

vi.mock('../components/auth/AuthModal', () => ({
  default: () => <div>认证弹窗内容</div>,
}));

vi.mock('../contexts/WebSocketContext', () => ({
  WebSocketProvider: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chat-runtime">{children}</div>
  ),
}));

vi.mock('../pages/Chat', () => ({
  default: () => <div>聊天页面</div>,
}));

beforeEach(() => {
  useAuthModalStore.setState({ isOpen: false, mode: 'login' });
  useAuthStore.setState({ isAuthenticated: true, isLoading: false });
});

afterEach(cleanup);

describe('App lazy runtime boundaries', () => {
  it('does not render the authentication module while the modal is closed', () => {
    render(<DeferredAuthModal />);

    expect(screen.queryByText('认证弹窗内容')).toBeNull();
  });

  it('loads the authentication module after the modal opens', async () => {
    useAuthModalStore.setState({ isOpen: true });
    render(<DeferredAuthModal />);

    expect(await screen.findByText('认证弹窗内容')).toBeInTheDocument();
  });

  it('mounts the WebSocket runtime around an authenticated Chat route', async () => {
    render(
      <MemoryRouter>
        <Suspense fallback={null}>
          <ProtectedChatRoute />
        </Suspense>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('chat-runtime')).toContainElement(
      screen.getByText('聊天页面'),
    );
  });
});
