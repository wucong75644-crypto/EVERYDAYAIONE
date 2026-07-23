import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { useWebSocketContext, WebSocketProvider } from '../WebSocketContext';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useAuthStore } from '../../stores/useAuthStore';
import { useMessageStore } from '../../stores/useMessageStore';
import { useTaskRestorationStore } from '../../stores/useTaskRestorationStore';

vi.mock('../../hooks/useWebSocket');
vi.mock('../../stores/useAuthStore');
vi.mock('../../stores/useTaskRestorationStore');
vi.mock('../../utils/taskRestoration');
vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: vi.fn(),
  normalizeMessage: (message: unknown) => message,
}));
vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

describe('WebSocketProvider tenant context', () => {
  const mockWs = {
    isConnected: true,
    isConnecting: false,
    subscribe: vi.fn(() => vi.fn()),
    subscribeTask: vi.fn(),
    unsubscribeTask: vi.fn(),
    send: vi.fn(),
  };
  const authState = { currentOrgId: 'org-a' };

  beforeEach(() => {
    vi.clearAllMocks();
    authState.currentOrgId = 'org-a';
    (useWebSocket as Mock).mockReturnValue(mockWs);
    (useAuthStore as unknown as Mock).mockImplementation(
      (selector: (state: typeof authState) => unknown) => selector(authState),
    );
    (useAuthStore as unknown as { getState: Mock }).getState = vi.fn(() => ({
      user: null,
      setUser: vi.fn(),
    }));
    (useMessageStore as unknown as { getState: Mock }).getState = vi.fn(() => ({
      messages: {},
    }));
    (useMessageStore as unknown as Mock).mockReturnValue(null);
    (useTaskRestorationStore as unknown as { getState: Mock }).getState = vi.fn(
      () => ({
        hydrateComplete: false,
        setPlaceholdersReady: vi.fn(),
      }),
    );
    (useTaskRestorationStore as unknown as { subscribe: Mock }).subscribe = vi.fn(
      () => vi.fn(),
    );
  });

  it('clears task mappings when organization changes', () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <WebSocketProvider>{children}</WebSocketProvider>
    );
    const { result, rerender } = renderHook(
      () => useWebSocketContext(),
      { wrapper },
    );

    act(() => {
      result.current.subscribeTaskWithMapping('task-1', 'conversation-1');
    });
    expect(mockWs.subscribeTask).toHaveBeenCalledTimes(1);

    authState.currentOrgId = 'org-b';
    rerender();

    act(() => {
      result.current.subscribeTaskWithMapping('task-1', 'conversation-1');
    });
    expect(mockWs.subscribeTask).toHaveBeenCalledTimes(2);
  });
});
