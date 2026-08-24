/** Ephemeral streaming UI actions extracted from the main Zustand slice factory. */

import type { StateCreator } from 'zustand';
import type { ToolStepPart } from '../../types/message';
import type { StreamingSlice, StreamingSliceDeps } from './streamingSlice';

type SliceState = StreamingSlice & StreamingSliceDeps;
type SetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[0];
type GetState = Parameters<StateCreator<SliceState, [], [], StreamingSlice>>[1];
type UiActionKeys =
  | 'appendStreamingThinking'
  | 'getStreamingThinking'
  | 'setAgentStepHint'
  | 'clearAgentStepHint'
  | 'setSuggestions'
  | 'clearSuggestions'
  | 'updateContentBlock'
  | 'setToolConfirmRequest'
  | 'setIsSending';

export function createStreamingUiActions(
  set: SetState,
  get: GetState,
): Pick<StreamingSlice, UiActionKeys> {
  return {
    updateContentBlock: (conversationId, toolCallId, updates) => {
      set((state) => {
        const streamingId = state.streamingMessages.get(conversationId);
        const list = state.optimisticMessages.get(conversationId);
        if (!streamingId || !list) return state;
        const targetIndex = list.findIndex((message) => message.id === streamingId);
        if (targetIndex === -1) return state;
        const target = list[targetIndex];
        const existingIndex = target.content.findIndex((block) => (
          block.type === 'tool_step' && block.tool_call_id === toolCallId
        ));
        const content = target.content.map((block) => (
          block.type === 'tool_step' && block.tool_call_id === toolCallId
            ? { ...block, ...updates }
            : block
        ));
        // 回放可能只包含 terminal tool_step，或 terminal 事件先于 running
        // 事件到达。此时用完整 block 建立它，而不是静默丢弃更新。
        if (existingIndex === -1 && updates.type === 'tool_step'
          && updates.tool_call_id === toolCallId) {
          content.push(updates as ToolStepPart);
        }
        const updatedList = [...list];
        updatedList[targetIndex] = { ...target, content };
        const optimisticMessages = new Map(state.optimisticMessages);
        optimisticMessages.set(conversationId, updatedList);
        return { optimisticMessages };
      });
    },
    appendStreamingThinking: (conversationId, chunk) => {
      set((state) => {
        const streamingThinking = new Map(state.streamingThinking);
        const previous = streamingThinking.get(conversationId) || '';
        streamingThinking.set(conversationId, previous + chunk);
        return { streamingThinking };
      });
    },
    getStreamingThinking: (conversationId) => (
      get().streamingThinking.get(conversationId) || ''
    ),
    setAgentStepHint: (conversationId, hint) => {
      set((state) => {
        const agentStepHint = new Map(state.agentStepHint);
        agentStepHint.set(conversationId, hint);
        return { agentStepHint };
      });
    },
    clearAgentStepHint: (conversationId) => {
      set((state) => {
        const agentStepHint = new Map(state.agentStepHint);
        agentStepHint.delete(conversationId);
        return { agentStepHint };
      });
    },
    setSuggestions: (conversationId, suggestions) => {
      set((state) => {
        const nextSuggestions = new Map(state.suggestions);
        nextSuggestions.set(conversationId, suggestions);
        return { suggestions: nextSuggestions };
      });
    },
    clearSuggestions: (conversationId) => {
      set((state) => {
        const nextSuggestions = new Map(state.suggestions);
        nextSuggestions.delete(conversationId);
        return { suggestions: nextSuggestions };
      });
    },
    setToolConfirmRequest: (request) => set({ toolConfirmRequest: request }),
    setIsSending: (sending) => set({ isSending: sending }),
  };
}
