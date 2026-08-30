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

export interface ChangeSetResourceAdapter {
  id: string;
  matches: (changeSet: ChangeSet) => boolean;
  getTitle?: (changeSet: ChangeSet) => string | undefined;
  getSummary?: (changeSet: ChangeSet) => string | undefined;
  getFields?: (changeSet: ChangeSet) => ChangeSetDisplayField[];
  getDiff?: (changeSet: ChangeSet) => ChangeSetDiffEntry[];
  getPlanSteps?: (changeSet: ChangeSet) => string[];
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
};

function scheduledFields(snapshot: Record<string, unknown>): ChangeSetDisplayField[] {
  return Object.entries(snapshot)
    .filter(([key]) => isDisplayableKey(key) && scheduledTaskLabels[key])
    .map(([key, value]) => ({ label: scheduledTaskLabels[key], value: displayValue(value) }));
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
  getTitle: (changeSet) => changeSet.proposed_snapshot.name as string | undefined,
  getSummary: (changeSet) => {
    const snapshot = changeSet.proposed_snapshot;
    const name = typeof snapshot.name === 'string' ? snapshot.name : '定时任务';
    return `${changeSet.operation === 'update' ? '修改' : '配置'}${name}`;
  },
  getFields: (changeSet) => scheduledFields(changeSet.proposed_snapshot),
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
};

registerChangeSetResourceAdapter(scheduledTaskChangeSetAdapter);
