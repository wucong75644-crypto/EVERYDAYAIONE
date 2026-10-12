import type { NotificationPolicy } from '../../types/scheduledTask';
import { Input } from '../ui/Input';

export function NotificationPolicyFields({ value, onChange }: {
  value: NotificationPolicy; onChange: (value: NotificationPolicy) => void;
}) {
  const conditional = value.mode === 'conditional';
  const numberField = (key: 'threshold' | 'window_minutes' | 'repeat_increment' | 'cooldown_minutes', label: string) => (
    <label key={key} className="block text-xs space-y-1">
      <span>{label}</span>
      <Input type="number" min={key === 'cooldown_minutes' ? 0 : 1} value={value[key] ?? ''}
        onChange={(event) => onChange({ ...value, [key]: event.target.value === '' ? undefined : Number(event.target.value) })} />
    </label>
  );
  return <div className="space-y-2 rounded-md border border-[var(--s-border-default)] p-3">
    <label className="block text-sm">通知方式
      <select aria-label="通知方式" value={value.mode} onChange={(event) => onChange(event.target.value === 'always'
        ? { mode: 'always' } : { mode: 'conditional', metric: '', comparison: 'value' })} className="w-full bg-transparent p-2 border rounded">
        <option value="always">每次执行都通知</option>
        <option value="conditional">达到条件才通知，无变化静默</option>
      </select>
    </label>
    {conditional && <>
      <label className="block text-xs">监测指标（如 likes、comments_count、sales）
        <Input value={value.metric || ''} onChange={(event) => onChange({ ...value, metric: event.target.value })} />
      </label>
      <label className="block text-xs">判断口径
        <select aria-label="判断口径" value={value.comparison || 'value'} className="w-full bg-transparent p-2 border rounded"
          onChange={(event) => onChange({ ...value, comparison: event.target.value as NotificationPolicy['comparison'] })}>
          <option value="value">当前数值</option><option value="delta">窗口内增长量</option><option value="percent">窗口内增长率（%）</option>
        </select>
      </label>
      {numberField('threshold', '达到门槛（大于或等于）')}
      {value.comparison !== 'value' && numberField('window_minutes', '比较窗口（分钟）')}
      {numberField('repeat_increment', '再次提醒需比上次提醒新增多少（可留空）')}
      {numberField('cooldown_minutes', '提醒最短间隔（分钟，可留空）')}
      <p className="text-xs text-[var(--s-text-tertiary)]">首次建立基线；同素材持续达标不会重复提醒。再次增长门槛留空时，需回落后重新达标且数据发生变化才再次提醒。缺少数据时不判断。</p>
    </>}
  </div>;
}
