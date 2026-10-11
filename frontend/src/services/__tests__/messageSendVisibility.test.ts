import { beforeEach, expect, it } from 'vitest';
import { useMessageStore } from '../../stores/useMessageStore';
import { mergeMessages } from '../../hooks/useUnifiedMessages';
import { applyOptimisticUpdate, processApiResponse, rollbackOnError, type SendContext, type SendOptions } from '../messageSendLifecycle';
import { ApiRequestError } from '../api';
const options: SendOptions = { conversationId: 'conv', content: [{ type: 'text', text: '第二条信息' }] };
const ctx: SendContext = { clientRequestId: 'request', userMessageId: 'user', assistantMessageId: 'reply', clientTaskId: 'task', now: new Date('2026-10-11T00:00:00Z'), placeholderCreatedAt: '2026-10-11T00:00:00.001Z' };
const visible = () => {
  const state = useMessageStore.getState();
  return mergeMessages(state.messages.conv, state.optimisticMessages.get('conv'));
};
beforeEach(() => useMessageStore.getState().reset());
it('keeps the user message through refresh until the server acknowledges it', () => {
  applyOptimisticUpdate(options, ctx);
  useMessageStore.getState().setMessagesForConversation('conv', []);
  expect(visible().map(message => message.id)).toEqual(['user', 'reply']);
  processApiResponse({ task_id: 'task', operation: 'send', generation_type: 'chat',
    user_message: { id: 'server-user', conversation_id: 'conv', role: 'user', content: options.content, status: 'completed', created_at: ctx.now.toISOString() },
    assistant_message: { id: 'reply', conversation_id: 'conv', role: 'assistant', content: [], status: 'pending', created_at: ctx.placeholderCreatedAt },
  }, options, ctx);
  expect(visible().map(message => message.id)).toEqual(['server-user', 'reply']);
});
it('removes the optimistic user message when credits explicitly reject the send', () => {
  applyOptimisticUpdate(options, ctx);
  rollbackOnError(new ApiRequestError('INSUFFICIENT_CREDITS', '积分不足', 400), options, ctx);
  expect(visible()).toEqual([]);
});
