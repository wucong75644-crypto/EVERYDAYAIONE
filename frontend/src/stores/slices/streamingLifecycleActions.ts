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
  | 'clearConversationStreaming'
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
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId) || [];
        const existingOptimisticIndex = list.findIndex((message) => message.id === messageId);
        if (existingOptimisticIndex >= 0) {
          optimisticMessages.set(conversationId, list.map((message, index) => (
            index === existingOptimisticIndex
              ? { ...message, status: 'streaming' }
              : message
          )));
        } else {
          const existing = state.messages[conversationId]?.find(
            (message) => message.id === messageId,
          );
          if (existing) {
            optimisticMessages.set(conversationId, [
              ...list,
              { ...existing, status: 'streaming' },
            ]);
          }
        }
        return { streamingMessages, optimisticMessages, isSending: true };
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
    clearConversationStreaming: (conversationId) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        streamingMessages.delete(conversationId);

        const optimisticMessages = new Map(state.optimisticMessages);
        const optimistic = optimisticMessages.get(conversationId);
        if (optimistic) {
          const transientStatuses = new Set(['pending', 'streaming', 'generating']);
          const retained = optimistic.filter((message) => (
            !(
              message.role === 'assistant'
              && transientStatuses.has(message.status)
              && (
                message.id === state.streamingMessages.get(conversationId)
                || !message.generation_params?.type
                || message.generation_params.type === 'chat'
              )
            )
          ));
          if (retained.length === 0) optimisticMessages.delete(conversationId);
          else optimisticMessages.set(conversationId, retained);
        }

        const streamingThinking = new Map(state.streamingThinking);
        streamingThinking.delete(conversationId);
        const agentStepHint = new Map(state.agentStepHint);
        agentStepHint.delete(conversationId);
        const suggestions = new Map(state.suggestions);
        suggestions.delete(conversationId);
        const toolConfirmRequest = state.toolConfirmRequest?.conversationId === conversationId
          ? null
          : state.toolConfirmRequest;

        return {
          streamingMessages,
          optimisticMessages,
          streamingThinking,
          agentStepHint,
          suggestions,
          toolConfirmRequest,
          isSending: streamingMessages.size > 0,
        };
      });
    },
    completeStreamingWithMessage: (conversationId, message) => {
      set((state) => {
        const streamingMessages = new Map(state.streamingMessages);
        const streamingId = streamingMessages.get(conversationId);
        const ownsStreamingSlot = streamingId === message.id;
        if (ownsStreamingSlot) {
          streamingMessages.delete(conversationId);
        }
        const optimisticMessages = new Map(state.optimisticMessages);
        const list = optimisticMessages.get(conversationId) || [];
        const targetIndex = list.findIndex((item) => item.id === message.id);
        const normalized = normalizeMessage(message);
        if (targetIndex === -1) {
          optimisticMessages.set(conversationId, [...list, normalized]);
        } else {
          const originalCreatedAt = list[targetIndex].created_at;
          optimisticMessages.set(
            conversationId,
            list
              .map((item, index) => (
                index === targetIndex
                  ? { ...normalized, created_at: originalCreatedAt }
                  : item
              ))
              .filter((item, index) => item.id !== message.id || index === targetIndex),
          );
        }
        if (!ownsStreamingSlot) {
          return { optimisticMessages };
        }
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
