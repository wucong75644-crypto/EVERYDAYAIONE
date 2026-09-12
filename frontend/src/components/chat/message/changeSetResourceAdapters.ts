import type { ChangeSet } from '../../../types/changeset';

export interface ChangeSetDisplayField {
  label: string;
  value: string;
}

export interface ChangeSetDiffEntry {
  label: string;
  before: string;
  after: string;
}

/**
 * 同一个 ChangeSet 契约可承载计划型变更、状态切换和破坏性变更；
 * 呈现方式由资源适配器决定，通用卡片不把业务操作写死。
 */
export interface ChangeSetPresentation {
  mode: 'planned' | 'state_change' | 'destructive';
  summaryTitle?: string;
  diffTitle?: string;
  checksTitle?: string;
  confirmationLabel?: string;
  cancellationLabel?: string;
  notice?: string;
  showDiff?: boolean;
  showPlan?: boolean;
  nonExecutionPreflightLabel?: string;
}

export interface ChangeSetResourceAdapter {
  id: string;
  matches: (changeSet: ChangeSet) => boolean;
  getTitle?: (changeSet: ChangeSet) => string | undefined;
  getSummary?: (changeSet: ChangeSet) => string | undefined;
  getFields?: (changeSet: ChangeSet) => ChangeSetDisplayField[];
  getDiff?: (changeSet: ChangeSet) => ChangeSetDiffEntry[];
  getPlanSteps?: (changeSet: ChangeSet) => string[];
  getPresentation?: (changeSet: ChangeSet) => ChangeSetPresentation | undefined;
}

const adapters: ChangeSetResourceAdapter[] = [];

export function registerChangeSetResourceAdapter(
  adapter: ChangeSetResourceAdapter,
): () => void {
  const existing = adapters.findIndex((item) => item.id === adapter.id);
  if (existing >= 0) adapters.splice(existing, 1, adapter);
  else adapters.push(adapter);
  return () => {
    const index = adapters.findIndex((item) => item.id === adapter.id);
    if (index >= 0 && adapters[index] === adapter) adapters.splice(index, 1);
  };
}

export function getChangeSetResourceAdapter(
  changeSet: ChangeSet,
): ChangeSetResourceAdapter | undefined {
  return adapters.find((adapter) => adapter.matches(changeSet));
}

const SENSITIVE_KEY = /(token|secret|password|authorization|credential|cookie|sql|trace|stack|exception|internal)/i;

export function isDisplayableKey(key: string): boolean {
  return !SENSITIVE_KEY.test(key);
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未设置';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  try {
    const result = JSON.stringify(value);
    return result === undefined ? '不可显示' : result;
  } catch {
    return '不可显示';
  }
}

const scheduledTaskLabels: Record<string, string> = {
  name: '任务名称', prompt: '执行内容', schedule_type: '计划类型',
  cron_expr: '计划表达式', run_at: '执行时间', timezone: '时区',
  push_target: '通知目标', retry_count: '重试次数', timeout_sec: '超时时间',
  status: '任务状态', next_run_at: '下次执行时间',
  max_credits: '每次积分上限',
};

function scheduledFields(snapshot: Record<string, unknown>, operation: string): ChangeSetDisplayField[] {
  const keys = operation === 'delete'
    ? ['name', 'schedule_type', 'cron_expr', 'run_at', 'timezone', 'push_target']
    : operation === 'pause' || operation === 'resume'
      ? ['name', 'status', 'next_run_at', 'schedule_type', 'cron_expr', 'run_at', 'timezone', 'push_target']
      : Object.keys(snapshot);
  return keys
    .filter((key) => isDisplayableKey(key) && scheduledTaskLabels[key])
    .filter((key) => key in snapshot)
    .map((key) => ({ label: scheduledTaskLabels[key], value: displayValue(snapshot[key]) }));
}

function directTaskFields(snapshot: Record<string, unknown>): ChangeSetDisplayField[] {
  const zone = typeof snapshot.timezone === 'string' ? snapshot.timezone : 'Asia/Shanghai';
  const date = (value: unknown) => {
    if (typeof value !== 'string' || Number.isNaN(Date.parse(value))) return '未设置';
    try { return new Date(value).toLocaleString('zh-CN', { timeZone: zone, hour12: false }); }
    catch { return value; }
  };
  const recipient = (value: unknown): string => {
    if (!value || typeof value !== 'object') return '未设置';
    const target = value as Record<string, unknown>;
    if (target.type === 'multi' && Array.isArray(target.targets)) return target.targets.map(recipient).join('、');
    if (target.type === 'web') return '网页通知';
    return String(target.chat_name || target.name || (target.type === 'wecom_group' ? '企业微信群' : '企业微信个人通知'));
  };
  let schedule = snapshot.schedule_type === 'once' ? `单次 · ${date(snapshot.run_at)}` : '自定义计划';
  const cron = typeof snapshot.cron_expr === 'string' ? snapshot.cron_expr.trim().split(/\s+/) : [];
  if (cron.length === 5 && /^\d+$/.test(cron[0]) && /^\d+$/.test(cron[1]) && cron[3] === '*') {
    const time = `${cron[1].padStart(2, '0')}:${cron[0].padStart(2, '0')}`;
    if (cron[2] === '*' && cron[4] === '*') schedule = `每天 ${time}`;
    else if (/^\d+$/.test(cron[2]) && cron[4] === '*') schedule = `每月 ${cron[2]} 日 ${time}`;
    else if (cron[2] === '*' && /^[0-6](,[0-6])*$/.test(cron[4])) {
      schedule = `每周${cron[4].split(',').map((day) => '日一二三四五六'[Number(day)]).join('、')} ${time}`;
    }
  }
  const fields: ChangeSetDisplayField[] = [
    { label: '任务名称', value: displayValue(snapshot.name) },
    { label: '执行内容', value: displayValue(snapshot.prompt) },
    { label: '时间安排', value: `${schedule}（${zone}）` },
    { label: '通知目标', value: recipient(snapshot.push_target) },
  ];
  if (snapshot.max_credits !== undefined) fields.push({ label: '每次积分上限', value: `${snapshot.max_credits} 积分` });
  if (snapshot.next_run_at && snapshot.schedule_enabled !== false && snapshot.status !== 'paused') {
    fields.push({ label: '下次执行时间', value: date(snapshot.next_run_at) });
  }
  return fields;
}

function scheduledDiff(changeSet: ChangeSet): ChangeSetDiffEntry[] {
  const base = changeSet.base_snapshot;
  const proposed = changeSet.proposed_snapshot;
  const keys = new Set([...Object.keys(base), ...Object.keys(proposed)]);
  const labels = { ...scheduledTaskLabels };
  if ((changeSet.policy_snapshot.submission as Record<string, unknown> | undefined)?.mode === 'apply_if_allowed') {
    Object.assign(labels, { execution_policy: '可调用工具及授权范围', data_scope: '数据范围', template_file: '模板文件' });
  }
  return [...keys]
    .filter((key) => isDisplayableKey(key) && labels[key])
    .filter((key) => JSON.stringify(base[key]) !== JSON.stringify(proposed[key]))
    .map((key) => ({
      label: labels[key],
      before: displayValue(base[key]),
      after: displayValue(proposed[key]),
    }));
}

/** 定时任务仅解释自己的字段，不把 scheduled_task_* 状态带入通用卡片。 */
export const scheduledTaskChangeSetAdapter: ChangeSetResourceAdapter = {
  id: 'scheduled-task',
  matches: (changeSet) => /scheduled.?task/i.test(changeSet.resource_type),
  getTitle: (changeSet) => ({
    create: '创建定时任务',
    update: '编辑定时任务',
    pause: '暂停定时任务',
    resume: '恢复定时任务',
    delete: '删除定时任务',
  }[changeSet.operation] || '定时任务变更'),
  getSummary: (changeSet) => {
    const snapshot = changeSet.proposed_snapshot;
    const name = typeof snapshot.name === 'string' ? snapshot.name : '定时任务';
    const action = ({ create: '创建', update: '修改', pause: '暂停', resume: '恢复', delete: '删除' } as Record<string, string>)[changeSet.operation] || '修改';
    if (changeSet.status === 'applied') return `已${action}「${name}」${changeSet.operation === 'pause' ? '的后续定时；已经开始的本次运行会继续完成。' : '。'}`;
    if (['failed', 'rejected', 'conflicted', 'cancelled', 'expired'].includes(changeSet.status)) return `本次${action}「${name}」未生效。`;
    const submission = changeSet.policy_snapshot.submission as Record<string, unknown> | undefined;
    if (submission?.mode === 'apply_if_allowed' && changeSet.status !== 'awaiting_approval') return `正在检查并${action}「${name}」，完成后更新结果。`;
    return ({
      create: `将创建「${name}」`,
      update: `将修改「${name}」的配置和执行路径`,
      pause: `将暂停「${name}」，暂停期间不会自动执行`,
      resume: `将恢复「${name}」的自动执行`,
      delete: `将永久删除「${name}」`,
    }[changeSet.operation] || `将变更「${name}」`);
  },
  getFields: (changeSet) => (changeSet.policy_snapshot.submission as Record<string, unknown> | undefined)?.mode === 'apply_if_allowed'
    ? directTaskFields(changeSet.proposed_snapshot)
    : scheduledFields(changeSet.proposed_snapshot, changeSet.operation),
  getDiff: scheduledDiff,
  getPlanSteps: (changeSet) => {
    const candidate = changeSet.plan_snapshot?.candidate as Record<string, unknown> | undefined;
    const steps = candidate?.steps || changeSet.plan_snapshot?.steps;
    if (!Array.isArray(steps)) return [];
    return steps.flatMap((step) => {
      if (!step || typeof step !== 'object') return [];
      const item = step as Record<string, unknown>;
      const text = item.intent ?? item.summary ?? item.description;
      return typeof text === 'string' && text.trim() ? [text] : [];
    });
  },
  getPresentation: (changeSet) => {
    if (changeSet.operation === 'delete') {
      return {
        mode: 'destructive',
        summaryTitle: '将删除的任务',
        checksTitle: '删除前检查',
        confirmationLabel: '确认删除',
        cancellationLabel: '保留任务',
        notice: '删除后该任务将停止执行，且不能从此处恢复。',
        showDiff: false,
        showPlan: false,
        nonExecutionPreflightLabel: '版本确认',
      };
    }
    if (changeSet.operation === 'pause' || changeSet.operation === 'resume') {
      const pausing = changeSet.operation === 'pause';
      return {
        mode: 'state_change',
        summaryTitle: pausing ? '将暂停的任务' : '将恢复的任务',
        diffTitle: '状态变化',
        checksTitle: pausing ? '暂停前检查' : '恢复前检查',
        confirmationLabel: pausing ? '确认暂停' : '确认恢复',
        cancellationLabel: pausing ? '保持启用' : '保持暂停',
        showPlan: false,
        nonExecutionPreflightLabel: '版本确认',
      };
    }
    return { mode: 'planned' };
  },
};

registerChangeSetResourceAdapter(scheduledTaskChangeSetAdapter);
