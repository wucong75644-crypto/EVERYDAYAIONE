import type { ResumeTaskResponse } from './message';

const accepted = new Set(['resumed', 'already_pending', 'enqueued', 'already_enqueued']);
type Delivery = {
  beginStreaming: (conversationId: string, messageId: string) => void;
  subscribe: (taskId: string, conversationId: string) => void;
  reload: () => Promise<void>;
};

/** Apply a real resume receipt; a continuation must never reset its source message. */
export async function applyTaskResume(
  response: ResumeTaskResponse, conversationId: string, sourceMessageId: string, delivery: Delivery,
): Promise<boolean> {
  if (!response.success) return false;
  const task = response.tasks?.find(item => accepted.has(item.outcome ?? '')
    && (!item.conversation_id || item.conversation_id === conversationId));
  if (!task) return false;
  const taskId = task.client_task_id || task.external_task_id || task.task_id;
  const separate = !!task.logical_task_id || ['enqueued', 'already_enqueued'].includes(task.outcome ?? '')
    || (!!task.assistant_message_id && task.assistant_message_id !== sourceMessageId);
  if (!separate) {
    if (!taskId) return false;
    delivery.beginStreaming(conversationId, sourceMessageId);
    delivery.subscribe(taskId, conversationId);
    return true;
  } else if (taskId && task.assistant_message_id && task.assistant_message_id !== sourceMessageId) {
    delivery.subscribe(taskId, conversationId);
  }
  // The resume already committed. A failed read must not suggest resending it.
  try { await delivery.reload(); } catch { /* Later WS/history refresh recovers it. */ }
  return true;
}
