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

function scheduledDiff(changeSet: ChangeSet): ChangeSetDiffEntry[] {
  const base = changeSet.base_snapshot;
  const proposed = changeSet.proposed_snapshot;
  const keys = new Set([...Object.keys(base), ...Object.keys(proposed)]);
  return [...keys]
    .filter((key) => isDisplayableKey(key) && scheduledTaskLabels[key])
    .filter((key) => JSON.stringify(base[key]) !== JSON.stringify(proposed[key]))
    .map((key) => ({
      label: scheduledTaskLabels[key],
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
    return ({
      create: `将创建「${name}」`,
      update: `将修改「${name}」的配置和执行路径`,
      pause: `将暂停「${name}」，暂停期间不会自动执行`,
      resume: `将恢复「${name}」的自动执行`,
      delete: `将永久删除「${name}」`,
    }[changeSet.operation] || `将变更「${name}」`);
  },
  getFields: (changeSet) => scheduledFields(changeSet.proposed_snapshot, changeSet.operation),
  getDiff: scheduledDiff,
  getPlanSteps: (changeSet) => {
    const steps = changeSet.plan_snapshot?.steps;
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
