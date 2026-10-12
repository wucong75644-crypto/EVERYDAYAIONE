import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { useConversationResultSync } from '../useConversationResultSync';
import { useMessageStore } from '../../stores/useMessageStore';
import { getMessages } from '../../services/message';
import type { Message } from '../../types/message';
vi.mock('../../services/message', () => ({ getMessages: vi.fn() }));
const message = (id: string, time = '2026-10-12T00:00:00Z') => ({
  id, conversation_id: 'chat', role: 'assistant', content: [], created_at: time, status: 'completed',
} as Message);
const reply = (...messages: Message[]) => ({ messages, has_more: false, total: messages.length });
beforeEach(() => {
  vi.clearAllMocks();
  useMessageStore.getState().reset();
  useMessageStore.getState().setCurrentConversation('chat', '测试');
  useMessageStore.getState().setMessagesForConversation('chat', [message('old')], true);
  vi.mocked(getMessages).mockResolvedValue(reply(message('old')));
});
afterEach(() => { vi.useRealTimers(); });
describe('durable conversation result reconciliation', () => {
  it('loads a result into the open chat on invalidation and deduplicates repeated events, preserving history', async () => {
    const { unmount } = renderHook(() => useConversationResultSync(true, 'org'));
    await waitFor(() => expect(getMessages).toHaveBeenCalledTimes(1));
    await act(async () => {});
    vi.mocked(getMessages).mockResolvedValue(reply(message('new', '2026-10-12T00:01:00Z')));
    act(() => useMessageStore.getState().markForceRefresh('chat'));
    await waitFor(() => expect(useMessageStore.getState().getCachedMessages('chat')?.messages.map(m => m.id)).toEqual(['old', 'new']));
    act(() => useMessageStore.getState().markForceRefresh('chat'));
    await act(async () => {});
    expect(useMessageStore.getState().getCachedMessages('chat')?.messages).toHaveLength(2);
    expect(useMessageStore.getState().getCachedMessages('chat')?.hasMore).toBe(true);
    unmount();
  });
  it('recovers on reconnect and returning to the page', async () => {
    const { rerender } = renderHook(({ connected }) => useConversationResultSync(connected, 'org'), { initialProps: { connected: false } });
    await act(async () => {});
    vi.mocked(getMessages).mockResolvedValue(reply(message('missed')));
    rerender({ connected: true });
    await waitFor(() => expect(useMessageStore.getState().getCachedMessages('chat')?.messages.some(m => m.id === 'missed')).toBe(true));
    vi.mocked(getMessages).mockResolvedValue(reply(message('visible')));
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await waitFor(() => expect(useMessageStore.getState().getCachedMessages('chat')?.messages.some(m => m.id === 'visible')).toBe(true));
  });
  it('polls only disconnected clients and stops when unmounted', async () => {
    vi.useFakeTimers();
    const { unmount, rerender } = renderHook(({ connected }) => useConversationResultSync(connected, 'org'), { initialProps: { connected: false } });
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(30_000); });
    expect(getMessages).toHaveBeenCalledTimes(2);
    rerender({ connected: true });
    await act(async () => {});
    const count = vi.mocked(getMessages).mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(getMessages).toHaveBeenCalledTimes(count);
    unmount();
  });
  it('fills a gap longer than one page using the existing cursor API', async () => {
    const page = Array.from({ length: 30 }, (_, index) => message(`gap-${30-index}`));
    vi.mocked(getMessages).mockResolvedValueOnce(reply(...page)).mockResolvedValueOnce(reply(message('gap-0'), message('old')));
    renderHook(() => useConversationResultSync(true, 'org'));
    await waitFor(() => expect(useMessageStore.getState().getCachedMessages('chat')?.messages).toHaveLength(32));
    expect(getMessages).toHaveBeenNthCalledWith(2, 'chat', 30, 0, 'gap-1', expect.any(AbortSignal));
  });
  it('does not replace an existing message with a stale response or write after unmount', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof getMessages>>) => void;
    vi.mocked(getMessages).mockReturnValue(new Promise(r => { resolve = r; }));
    const { unmount } = renderHook(() => useConversationResultSync(true, 'org'));
    const updated = { ...message('old'), content: [{ type: 'text', text: '最新内容' }] } as Message;
    act(() => useMessageStore.getState().setMessagesForConversation('chat', [updated]));
    await act(async () => resolve(reply(message('old'), message('new'))));
    expect(useMessageStore.getState().getCachedMessages('chat')?.messages.find(m => m.id === 'old')?.content).toEqual(updated.content);
    vi.mocked(getMessages).mockReturnValue(new Promise(r => { resolve = r; }));
    act(() => useMessageStore.getState().markForceRefresh('chat'));
    unmount();
    await act(async () => resolve(reply(message('late'))));
    expect(useMessageStore.getState().getCachedMessages('chat')?.messages.some(m => m.id === 'late')).toBe(false);
  });
  it('keeps the refresh marker on failure and retries on returning to the page', async () => {
    vi.mocked(getMessages).mockRejectedValueOnce(new Error('offline'));
    useMessageStore.getState().markForceRefresh('chat');
    renderHook(() => useConversationResultSync(true, 'org'));
    await act(async () => {});
    expect(useMessageStore.getState().needsForceRefresh('chat')).toBe(true);
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await waitFor(() => expect(useMessageStore.getState().needsForceRefresh('chat')).toBe(false));
  });

});
