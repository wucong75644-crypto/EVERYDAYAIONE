/**
 * useMessageLoader cursor 分页测试
 *
 * 重点验证 V3 升级后的 loadMore 必须用 before_id（cursor 分页），
 * 不再用 offset，避免：
 * 1. 翻深页变慢（offset 深页是 O(n)）
 * 2. 翻页期间新消息插入导致重复/丢消息
 *
 * Mock 策略：mock services/message.getMessages 验证传参，
 * 同时 mock useMessageStore 避免真实状态依赖。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useMessageLoader } from '../useMessageLoader';
import { getMessages } from '../../services/message';
import { useMessageStore } from '../../stores/useMessageStore';

vi.mock('../../services/message', () => ({
  getMessages: vi.fn(),
}));

vi.mock('../../stores/useMessageStore', () => {
  const mockStore = {
    getCachedMessages: vi.fn(),
    prependMessages: vi.fn(),
    setMessagesForConversation: vi.fn(),
  };
  const useMessageStoreMock = vi.fn(() => mockStore) as unknown as {
    (): typeof mockStore;
    getState: () => typeof mockStore;
  };
  useMessageStoreMock.getState = () => mockStore;
  return {
    useMessageStore: useMessageStoreMock,
    normalizeMessage: (m: unknown) => m,
  };
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useMessageLoader.loadMore — cursor 分页', () => {
  it('loadMore 使用 before_id 而非 offset 翻页', async () => {
    const oldestId = 'msg-oldest-id';
    const cachedMessages = [
      { id: oldestId, content: '最早', created_at: '2024-01-01T00:00:00Z' },
      { id: 'msg-2', content: '次早', created_at: '2024-01-02T00:00:00Z' },
    ];

    const store = useMessageStore.getState();
    vi.mocked(store.getCachedMessages).mockReturnValue({
      messages: cachedMessages,
      hasMore: true,
    } as ReturnType<typeof store.getCachedMessages>);

    vi.mocked(getMessages).mockResolvedValue({
      messages: [
        { id: 'older-1', content: '更老', created_at: '2023-12-31T00:00:00Z' },
      ],
      total: 1,
      has_more: false,
    } as Awaited<ReturnType<typeof getMessages>>);

    const { result } = renderHook(() =>
      useMessageLoader({ conversationId: 'conv-123' }),
    );

    await act(async () => {
      await result.current.loadMore();
    });

    // 关键验证：getMessages 必须用 oldestId 作为 before_id 传入
    // 第 4 个参数是 beforeId，第 3 个 offset 必须传 0
    expect(getMessages).toHaveBeenCalledWith(
      'conv-123',
      30, // LOAD_MORE_LIMIT
      0, // offset 强制 0
      oldestId, // before_id cursor
    );
  });

  it('loadMore 不会调用任何 offset > 0 的 getMessages', async () => {
    const cachedMessages = Array.from({ length: 30 }, (_, i) => ({
      id: `msg-${i}`,
      content: `消息${i}`,
      created_at: new Date(2024, 0, i + 1).toISOString(),
    }));

    const store = useMessageStore.getState();
    vi.mocked(store.getCachedMessages).mockReturnValue({
      messages: cachedMessages,
      hasMore: true,
    } as ReturnType<typeof store.getCachedMessages>);

    vi.mocked(getMessages).mockResolvedValue({
      messages: [],
      total: 0,
      has_more: false,
    } as Awaited<ReturnType<typeof getMessages>>);

    const { result } = renderHook(() =>
      useMessageLoader({ conversationId: 'conv-x' }),
    );

    await act(async () => {
      await result.current.loadMore();
    });

    // 验证 offset 参数永远是 0（cursor 模式）
    const callArgs = vi.mocked(getMessages).mock.calls[0];
    expect(callArgs[2]).toBe(0); // offset
    expect(callArgs[3]).toBe('msg-0'); // beforeId 是最旧那条
  });

  it('hasMore=false 时 loadMore 跳过请求', async () => {
    const store = useMessageStore.getState();
    vi.mocked(store.getCachedMessages).mockReturnValue({
      messages: [{ id: 'a' }],
      hasMore: false,
    } as ReturnType<typeof store.getCachedMessages>);

    const { result } = renderHook(() =>
      useMessageLoader({ conversationId: 'conv' }),
    );

    await act(async () => {
      await result.current.loadMore();
    });

    expect(getMessages).not.toHaveBeenCalled();
  });

  it('cached.messages 为空时 loadMore 跳过（无 cursor 可用）', async () => {
    const store = useMessageStore.getState();
    vi.mocked(store.getCachedMessages).mockReturnValue({
      messages: [],
      hasMore: true,
    } as ReturnType<typeof store.getCachedMessages>);

    const { result } = renderHook(() =>
      useMessageLoader({ conversationId: 'conv' }),
    );

    await act(async () => {
      await result.current.loadMore();
    });

    expect(getMessages).not.toHaveBeenCalled();
  });

  it('loadMore 返回 LOAD_MORE_LIMIT 条数据时设置 hasMore=true', async () => {
    const store = useMessageStore.getState();
    vi.mocked(store.getCachedMessages).mockReturnValue({
      messages: [{ id: 'oldest', content: 'x', created_at: '2024-01-01T00:00:00Z' }],
      hasMore: true,
    } as ReturnType<typeof store.getCachedMessages>);

    // 返回正好 LOAD_MORE_LIMIT 条 → 还有更多
    const fullPage = Array.from({ length: 30 }, (_, i) => ({
      id: `older-${i}`,
      content: `更老${i}`,
      created_at: '2023-01-01T00:00:00Z',
    }));
    vi.mocked(getMessages).mockResolvedValue({
      messages: fullPage,
      total: 30,
      has_more: true,
    } as Awaited<ReturnType<typeof getMessages>>);

    const { result } = renderHook(() =>
      useMessageLoader({ conversationId: 'c' }),
    );

    await act(async () => {
      await result.current.loadMore();
    });

    await waitFor(() => {
      expect(result.current.hasMore).toBe(true);
    });
  });
});
