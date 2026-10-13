import type { Message } from '../types/message';

export function staleImageAttempt(existing: Message | undefined, taskId: string | undefined,
  incoming?: Record<string, unknown>): boolean {
  const params = incoming?.generation_params as Record<string, unknown> | undefined;
  if (existing?.generation_params?.origin !== 'chat_image' && params?.origin !== 'chat_image') return false;
  const incomingTask = params?.task_id;
  if (taskId && incomingTask && incomingTask !== taskId) return true;
  const currentTask = existing?.generation_params?.task_id;
  if (!currentTask || !taskId || currentTask === taskId) return false;
  const incomingRevision = incoming?.context_revision;
  if (typeof incomingRevision === 'number' && typeof existing?.context_revision === 'number'
      && incomingRevision !== existing.context_revision) return incomingRevision < existing.context_revision;
  // A new attempt can finish before its HTTP acceptance reaches this tab.
  return params?.source_task_id !== currentTask;
}

