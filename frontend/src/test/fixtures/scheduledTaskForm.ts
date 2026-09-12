import type { FormPart } from '../../types/message';

// Synthetic counterpart of the persisted form from the 2026-09-12 incident.
export const scheduledTaskForm: FormPart = {
  type: 'form', form_type: 'scheduled_task_create', form_id: 'task-create-test',
  title: '补充任务信息', submit_text: '创建任务', status: 'open',
  fields: [
    { name: 'name', type: 'hidden', label: '任务名称', default_value: '测试日报' },
    { name: 'prompt', type: 'textarea', label: '执行内容', required: true, default_value: '汇总A店昨天销售' },
    { name: 'schedule_type', type: 'select', label: '执行频率', required: true,
      default_value: 'daily', options: [{ label: '每天', value: 'daily' }, { label: '仅一次', value: 'once' }] },
    { name: 'time_str', type: 'time', label: '执行时间', required: true, default_value: '',
      visible_when: { field: 'schedule_type', value: 'once', not: true } },
    { name: 'run_at', type: 'datetime-local', label: '执行日期和时间（北京时间）', required: true,
      default_value: '', visible_when: { field: 'schedule_type', value: 'once' } },
    { name: 'push_target', type: 'hidden', label: '推送到', default_value: '{"type":"web","user_id":"test-user"}' },
    { name: '_submission_mode', type: 'hidden', label: '', default_value: 'apply_if_allowed' },
  ],
};
