import { describe, expect, it } from 'vitest';
import { create } from 'zustand';
import {
  createMessageSlice,
  type MessageSlice,
  type MessageSliceDeps,
} from '../messageSlice';

type TestStore = MessageSlice & MessageSliceDeps;

function message(status: 'streaming' | 'interrupted') {
  return {
    id: 'message-1',
    conversation_id: 'conversation-1',
    role: 'assistant' as const,
    content: [{ type: 'text' as const, text: 'partial' }],
    status,
    created_at: '2026-08-30T00:00:00.000Z',
  };
}

describe('messageSlice.updateMessage', () => {
  it('updates persisted and optimistic copies of the same streaming message', () => {
    const store = create<TestStore>()((set, get, api) => ({
      optimisticMessages: new Map([['conversation-1', [message('streaming')]]]),
      ...createMessageSlice(set, get, api),
    }));
    store.setState({ messages: { 'conversation-1': [message('streaming')] } });

    store.getState().updateMessage('message-1', { status: 'interrupted' });

    expect(store.getState().messages['conversation-1'][0].status).toBe('interrupted');
    expect(store.getState().optimisticMessages.get('conversation-1')![0].status)
      .toBe('interrupted');
  });
});
