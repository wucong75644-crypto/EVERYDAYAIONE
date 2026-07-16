/** Optimistic message actions extracted from the main Zustand slice factory. */

import type { StateCreator } from 'zustand';
import { normalizeMessage } from '../../utils/messageUtils';
import type { StreamingSlice, StreamingSliceDeps } from './streamingSlice';

type SliceState = StreamingSlice & StreamingSliceDeps;
type SetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[0];
type GetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[1];
type ActionKeys =
  | 'addOptimisticMessage'
  | 'addOptimisticUserMessage'
  | 'updateOptimisticMessageId'
  | 'addErrorMessage'
  | 'removeOptimisticMessage'
  | 'getOptimisticMessages';

export function createOptimisticMessageActions(
  set: SetState,
  get: GetState,
): Pick<StreamingSlice, ActionKeys> {
  const addMessage = (setSending: boolean): StreamingSlice['addOptimisticMessage'] => (
    conversationId,
    message,
  ) => {
    set((state) => {
      const optimisticMessages = new Map(state.optimisticMessages);
      const list = optimisticMessages.get(conversationId) || [];
      if (list.some((item) => item.id === message.id)) return state;
      optimisticMessages.set(conversationId, [...list, normalizeMessage(message)]);
      return setSending ? { optimisticMessages, isSending: true } : { optimisticMessages };
    });
  };

  return {
    addOptimisticMessage: addMessage(false),
    addOptimisticUserMessage: addMessage(true),
    updateOptimisticMessageId: (conversationId, clientRequestId, newId) => {
      set((state) => {
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId);
        if (!list) return state;
        optimisticMessages.set(conversationId, list.map((message) => (
          message.client_request_id === clientRequestId
            ? { ...message, id: newId, status: 'completed' as const }
            : message
        )));
        return { optimisticMessages };
      });
    },
    addErrorMessage: (conversationId, errorMessage) => {
      set((state) => {
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId) || [];
        if (list.some((message) => message.id === errorMessage.id)) return state;
        const streamingMessages = new Map(state.streamingMessages);
        const streamingId = streamingMessages.get(conversationId);
        const filtered = list.filter((message) => message.id !== streamingId);
        optimisticMessages.set(conversationId, [...filtered, normalizeMessage(errorMessage)]);
        streamingMessages.delete(conversationId);
        return { optimisticMessages, streamingMessages, isSending: false };
      });
    },
    removeOptimisticMessage: (conversationId, messageId) => {
      set((state) => {
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId);
        if (!list) return state;
        optimisticMessages.set(
          conversationId,
          list.filter((message) => message.id !== messageId),
        );
        return { optimisticMessages };
      });
    },
    getOptimisticMessages: (conversationId) => (
      get().optimisticMessages.get(conversationId) || []
    ),
  };
}
