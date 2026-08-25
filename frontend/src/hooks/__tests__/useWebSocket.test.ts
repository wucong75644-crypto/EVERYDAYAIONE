import { act, renderHook } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { isAccessTokenExpired, useWebSocket } from '../useWebSocket';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  send = vi.fn();
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  close(code = 1000, reason = ''): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
}

function tokenWithExp(exp: number): string {
  const payload = btoa(JSON.stringify({ exp }));
  return `header.${payload}.signature`;
}

describe('isAccessTokenExpired', () => {
  it('识别已经过期的 JWT', () => {
    expect(isAccessTokenExpired(tokenWithExp(1_000), 2_000_000)).toBe(true);
  });

  it('在过期前的安全窗口内提前刷新', () => {
    expect(isAccessTokenExpired(tokenWithExp(2_030), 2_000_000)).toBe(true);
  });

  it('不过期的 JWT 不触发刷新', () => {
    expect(isAccessTokenExpired(tokenWithExp(2_100), 2_000_000)).toBe(false);
  });

  it('无法解析的 token 交给服务端处理', () => {
    expect(isAccessTokenExpired('not-a-jwt', 2_000_000)).toBe(false);
  });
});

describe('useWebSocket connection coordination', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
    localStorage.setItem('access_token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it('ignores a stale socket close after a newer socket is connected', async () => {
    const { result, unmount } = renderHook(() => useWebSocket());
    const first = MockWebSocket.instances[0];

    await act(async () => {
      first.open();
      first.close(1006, 'network');
      vi.advanceTimersByTime(1000);
    });

    const second = MockWebSocket.instances[1];
    expect(second).toBeDefined();

    await act(async () => {
      second.open();
      // 模拟旧浏览器事件晚到，不能把新连接改回 disconnected。
      first.onclose?.({ code: 1006, reason: 'late stale event' });
    });

    expect(result.current.isConnected).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(2);
    unmount();
  });
});
