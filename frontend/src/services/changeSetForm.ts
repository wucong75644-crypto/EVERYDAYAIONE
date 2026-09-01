import type { ScheduledTaskChangeRequest } from '../types/scheduledTask';

/**
 * 仅供独立 API 调用方构造定时任务 ChangeSet 请求。
 *
 * 聊天表单不使用此模块：它们始终经由 WebSocket 服务端生成 ChangeSet，并由
 * 服务端持久化消息展示引用，以消除客户端双分流。
 */
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
      // 保留原值，由服务端返回安全的字段校验信息。
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
    message_id: identity.messageId,
    conversation_id: identity.conversationId,
    form_id: identity.formId,
  };
  if (request.operation === 'update' && typeof formData.task_id === 'string' && formData.task_id) {
    request.task_id = formData.task_id;
  }
  return request;
}
