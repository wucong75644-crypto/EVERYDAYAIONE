import { describe, expect, it } from 'vitest';
import { buildScheduledTaskChangeRequest, isScheduledTaskChangeSetForm } from '../changeSetForm';

describe('changeSetForm', () => {
  const identity = { conversationId: 'conv-1', messageId: 'msg-1', formId: 'form-1' };

  it('builds a create request with normalized form values and a stable idempotency key', () => {
    const formData = {
      name: '日报', prompt: '查询数据', schedule_type: 'daily', time_str: '09:00',
      weekdays: ['1', '3'], push_target: '{"type":"web"}',
    };
    const request = buildScheduledTaskChangeRequest('scheduled_task_create', formData, identity);

    expect(request.operation).toBe('create');
    expect(request.definition).toMatchObject({
      push_target: { type: 'web' }, weekdays: [1, 3], timezone: 'Asia/Shanghai',
    });
    expect(request).toMatchObject({
      message_id: 'msg-1', conversation_id: 'conv-1', form_id: 'form-1',
    });
    expect(buildScheduledTaskChangeRequest('scheduled_task_create', formData, identity).idempotency_key)
      .toBe(request.idempotency_key);
  });

  it('keeps the task id at the request level for updates', () => {
    const request = buildScheduledTaskChangeRequest('scheduled_task_update', {
      task_id: 'task-1', name: '新日报', prompt: '查询数据', schedule_type: 'daily',
      time_str: '10:00', push_target: { type: 'web' },
    }, identity);

    expect(request.operation).toBe('update');
    expect(request.task_id).toBe('task-1');
    expect(request.definition).not.toHaveProperty('task_id');
  });

  it('only treats create and update forms as ChangeSet form routes', () => {
    expect(isScheduledTaskChangeSetForm('scheduled_task_create')).toBe(true);
    expect(isScheduledTaskChangeSetForm('scheduled_task_update')).toBe(true);
    expect(isScheduledTaskChangeSetForm('scheduled_task_confirm')).toBe(false);
  });
});
