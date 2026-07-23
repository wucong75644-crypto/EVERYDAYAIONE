import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '../../stores/useAuthStore';
import { useWebSocket } from '../useWebSocket';

vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../../utils/tokenManager', () => ({
  logoutOnce: vi.fn(),
}));

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  closed = false;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(message: string): void {
    this.sent.push(message);
  }

  close(code = 1000, reason = ''): void {
    this.closed = true;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

describe('useWebSocket tenant connection', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('access_token', 'token-1');
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
    useAuthStore.setState({
      user: { id: 'user-1' } as never,
      isAuthenticated: true,
      isLoading: false,
      currentOrgId: 'org-a',
      currentOrg: {
        org_id: 'org-a',
        name: 'A',
        role: 'member',
      },
      organizations: [],
    });
  });

  it('closes the old connection and reconnects for the new organization', async () => {
    const { result } = renderHook(() => useWebSocket());

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    expect(MockWebSocket.instances[0].url).toContain('org_id=org-a');

    act(() => {
      result.current.subscribeTask('org-a-task');
    });

    act(() => {
      useAuthStore.getState().setCurrentOrg({
        org_id: 'org-b',
        name: 'B',
        role: 'member',
      });
    });

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(2);
    });
    expect(MockWebSocket.instances[0].closed).toBe(true);
    expect(MockWebSocket.instances[1].url).toContain('org_id=org-b');

    act(() => {
      MockWebSocket.instances[1].readyState = MockWebSocket.OPEN;
      MockWebSocket.instances[1].onopen?.();
    });
    expect(MockWebSocket.instances[1].sent).toEqual([]);
  });
});
