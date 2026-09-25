import { useEffect, useRef, useState } from 'react';
import { scheduledTaskService } from '../../services/scheduledTask';
import type { ScheduledSkillChoice } from '../../types/scheduledTask';

interface Props {
  taskId?: string;
  selected: ScheduledSkillChoice[];
  onChange: (skills: ScheduledSkillChoice[]) => void;
  disabled?: boolean;
}

export function ScheduledSkillPicker({ taskId, selected, onChange, disabled }: Props) {
  const [options, setOptions] = useState<ScheduledSkillChoice[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);
  useEffect(() => {
    requestId.current += 1;
    setOptions(null);
    setLoading(false);
    setError('');
    return () => { requestId.current += 1; };
  }, [taskId]);

  async function load() {
    const id = ++requestId.current;
    setLoading(true);
    setError('');
    try {
      const result = await scheduledTaskService.skillOptions(taskId);
      if (id === requestId.current) setOptions(result);
    } catch {
      if (id === requestId.current) setError('无法读取可用 Skill，请重试。');
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }

  return <div className="space-y-2 rounded-lg border border-[var(--c-input-border)] p-3 text-sm">
    <div className="font-medium text-[var(--s-text-secondary)]">固定 Skill（可选）</div>
    <p className="text-xs text-[var(--s-text-tertiary)]">选择后固定版本。新版发布不会自动替换；修改仅对新配置生效。</p>
    {selected.map((skill) => <div key={skill.skill_id} className="flex items-center justify-between gap-2">
      <span>{skill.name || skill.skill_id} · {skill.revision}</span>
      <button type="button" disabled={disabled} className="text-[var(--s-text-tertiary)] hover:text-red-600"
        onClick={() => onChange(selected.filter((s) => s.skill_id !== skill.skill_id))}
        aria-label={`移除 ${skill.name || skill.skill_id}`}>移除</button>
    </div>)}
    <button type="button" disabled={disabled || loading} onClick={() => void load()}
      className="text-blue-600 disabled:opacity-50">{loading ? '读取中…' : '选择固定 Skill'}</button>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {options?.length === 0 && <p className="text-[var(--s-text-tertiary)]">没有已审核且允许用于此计划任务的 Skill。</p>}
    {options?.map((skill) => {
      const current = selected.find((s) => s.skill_id === skill.skill_id);
      return <div key={`${skill.skill_id}:${skill.revision}`} className="flex items-center justify-between gap-3">
        <div><div>{skill.name} · {skill.revision}</div><p className="text-xs text-[var(--s-text-tertiary)]">{skill.description}</p></div>
        <button type="button" className="shrink-0 text-blue-600 disabled:opacity-50"
          disabled={disabled || current?.revision === skill.revision || (!current && selected.length >= 4)}
          onClick={() => onChange([...selected.filter((s) => s.skill_id !== skill.skill_id), skill])}>
          {current?.revision === skill.revision ? '已选择' : current ? '更换版本' : '选择'}
        </button>
      </div>;
    })}
  </div>;
}
