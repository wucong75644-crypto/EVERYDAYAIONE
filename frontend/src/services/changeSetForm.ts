import type { ScheduledTaskChangeRequest } from '../types/scheduledTask';

const CHANGESET_FORM_TYPES = new Set(['scheduled_task_create', 'scheduled_task_update']);

export function isScheduledTaskChangeSetForm(formType: string): boolean {
  return CHANGESET_FORM_TYPES.has(formType);
}

function normalizeDefinition(formData: Record<string, unknown>): Record<string, unknown> {
  const definition = { ...formData };
  const pushTarget = definition.push_target;
  if (typeof pushTarget === 'string') {
    try {
      definition.push_target = JSON.parse(pushTarget) as unknown;
    } catch {
      // Keep the original value so the server can return a safe validation message.
    }
  }
  if (Array.isArray(definition.weekdays)) {
    definition.weekdays = definition.weekdays.map((day) => Number(day));
  }
  definition.timezone = definition.timezone || 'Asia/Shanghai';
  delete definition.task_id;
  return definition;
}

function shortHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function buildScheduledTaskChangeRequest(
  formType: string,
  formData: Record<string, unknown>,
  identity: { conversationId: string; messageId: string; formId: string },
): ScheduledTaskChangeRequest {
  const definition = normalizeDefinition(formData);
  const serialized = JSON.stringify({ formType, definition });
  const request: ScheduledTaskChangeRequest = {
    operation: formType === 'scheduled_task_update' ? 'update' : 'create',
    definition,
    idempotency_key: `chat-form:${identity.conversationId}:${identity.messageId}:${identity.formId}:${shortHash(serialized)}`,
  };
  if (request.operation === 'update' && typeof formData.task_id === 'string' && formData.task_id) {
    request.task_id = formData.task_id;
  }
  return request;
}
