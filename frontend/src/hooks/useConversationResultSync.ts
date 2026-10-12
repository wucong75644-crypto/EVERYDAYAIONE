import { useEffect } from 'react';
import { getMessages } from '../services/message';
import { useMessageStore, normalizeMessage } from '../stores/useMessageStore';
import { logger } from '../utils/logger';

/** Reconcile durable messages without replacing loaded history or active streams. */
export function useConversationResultSync(connected: boolean, orgId: string | null) {
  useEffect(() => {
    let disposed = false;
    let pending = false;
    let requested = false;
    const controller = new AbortController();
    const sync = async () => {
      if (disposed || document.visibilityState === 'hidden') return;
      if (pending) { requested = true; return; }
      const initial = useMessageStore.getState();
      const id = initial.currentConversationId;
      const cached = id ? initial.getCachedMessages(id) : null;
      // Initial loading belongs to MessageArea, not this reconciliation path.
      if (!id || !cached?.messages.length) return;
      pending = true;
      try {
        const boundary = new Set(cached.messages.map(message => message.id));
        const fetched: ReturnType<typeof normalizeMessage>[] = [];
        let beforeId: string | undefined;
        // Walk back to cached history when an absence spans multiple pages.
        while (!disposed) {
          const response = await getMessages(id, 30, 0, beforeId, controller.signal);
          if (disposed) return;
          fetched.push(...response.messages.map(normalizeMessage));
          if (response.messages.some(message => boundary.has(message.id)) || response.messages.length < 30) break;
          const next = response.messages.at(-1)?.id;
          if (!next || next === beforeId) break;
          beforeId = next;
        }
        if (disposed) return;
        const store = useMessageStore.getState();
        const current = store.getCachedMessages(id);
        if (!current) return;
        const known = new Set(current.messages.map(message => message.id));
        const incoming = fetched.reverse().filter(message => {
          if (known.has(message.id)) return false;
          known.add(message.id);
          return true;
        });
        if (incoming.length) {
          // Existing IDs remain untouched: a concurrent token stream or form
          // update must never be rolled back by a slower history response.
          store.setMessagesForConversation(id, [...current.messages, ...incoming].sort(
            (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
          ), current.hasMore);
          if (store.currentConversationId !== id) store.markConversationUnread(id);
        }
        store.clearForceRefresh(id);
      } catch (error) {
        if (!disposed) logger.error('conversationSync', 'Message reconciliation failed', error);
      } finally {
        pending = false;
        if (requested && !disposed) { requested = false; void sync(); }
      }
    };
    const unsubscribe = useMessageStore.subscribe((state, previous) => {
      const id = state.currentConversationId;
      if (id && state.forceRefreshConversations.has(id) &&
          (state.forceRefreshConversations !== previous.forceRefreshConversations || id !== previous.currentConversationId)) {
        void sync();
      }
    });
    const onVisible = () => { void sync(); };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onVisible);
    // Reconnection/initial connection catches events missed before subscription.
    void sync();
    // Healthy sockets use events; only disconnected clients need polling.
    const timer = !connected ? window.setInterval(onVisible, 30_000) : undefined;
    return () => {
      disposed = true;
      controller.abort();
      unsubscribe();
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onVisible);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [connected, orgId]);
}
