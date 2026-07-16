/** Streaming lifecycle actions extracted from the main Zustand slice factory. */

import type { StateCreator } from 'zustand';
import { normalizeMessage } from '../../utils/messageUtils';
import type { StreamingSlice, StreamingSliceDeps } from './streamingSlice';

type SliceState = StreamingSlice & StreamingSliceDeps;
type SetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[0];
type GetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[1];
type ActionKeys =
  | 'startStreaming'
  | 'registerStreamingId'
  | 'completeStreaming'
  | 'completeStreamingWithMessage'
  | 'getStreamingMessageId';

export function createStreamingLifecycleActions(
  set: SetState,
  get: GetState,
): Pick<StreamingSlice, ActionKeys> {
  return {
    startStreaming: (conversationId, messageId, options) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        streamingMessages.set(conversationId, messageId);
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId) || [];
        if (!list.some((message) => message.id === messageId)) {
          optimisticMessages.set(conversationId, [...list, {
            id: messageId,
            conversation_id: conversationId,
            role: 'assistant',
            content: [{ type: 'text', text: options?.initialContent ?? '' }],
            status: 'streaming',
            created_at: options?.createdAt || new Date().toISOString(),
            generation_params: options?.generationParams,
          }]);
        }
        return { streamingMessages, optimisticMessages, isSending: true };
      });
    },
    registerStreamingId: (conversationId, messageId) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        streamingMessages.set(conversationId, messageId);
        return { streamingMessages, isSending: true };
      });
    },
    completeStreaming: (conversationId) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        streamingMessages.delete(conversationId);
        const streamingThinking = new Map(state.streamingThinking);
        streamingThinking.delete(conversationId);
        const agentStepHint = new Map(state.agentStepHint);
        agentStepHint.delete(conversationId);
        const suggestions = new Map(state.suggestions);
        suggestions.delete(conversationId);
        return { streamingMessages, streamingThinking, agentStepHint, suggestions, isSending: false };
      });
    },
    completeStreamingWithMessage: (conversationId, message) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        const streamingId = streamingMessages.get(conversationId);
        streamingMessages.delete(conversationId);
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId) || [];
        const filtered = list.filter((item) => item.id !== streamingId);
        optimisticMessages.set(conversationId, [...filtered, normalizeMessage(message)]);
        const streamingThinking = new Map(state.streamingThinking);
        streamingThinking.delete(conversationId);
        const agentStepHint = new Map(state.agentStepHint);
        agentStepHint.delete(conversationId);
        const suggestions = new Map(state.suggestions);
        suggestions.delete(conversationId);
        return {
          streamingMessages, optimisticMessages, streamingThinking,
          agentStepHint, suggestions, isSending: false,
        };
      });
    },
    getStreamingMessageId: (conversationId) => (
      get().streamingMessages.get(conversationId) || null
    ),
  };
}
