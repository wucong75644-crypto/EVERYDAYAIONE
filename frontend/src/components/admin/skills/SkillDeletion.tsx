import { useEffect, useState } from 'react';
import { checkSkillDeletion, type SkillDeletionCheck } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';

export function SkillDeletion({ orgId, packageId, version, busy, onDelete }: {
  orgId: string; packageId: string; version: number; busy: boolean; onDelete: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const requestKey = `${orgId}:${packageId}:${version}:${attempt}`;
  const [result, setResult] = useState<{ key: string; check: SkillDeletionCheck | null } | null>(null);
  const checking = result?.key !== requestKey;
  const check = checking ? null : result.check;
  useEffect(() => {
    let cancelled = false;
    checkSkillDeletion(orgId, packageId)
      .then(value => { if (!cancelled) setResult({ key: requestKey, check: value }); })
      .catch(() => { if (!cancelled) setResult({ key: requestKey, check: null }); });
    return () => { cancelled = true; };
  }, [orgId, packageId, requestKey]);
  const explanation = checking ? '正在检查是否可以安全删除…'
    : !check ? '安全检查暂不可用，请重新检查。'
      : check.allowed ? '删除后从 Skill 库移除，保留历史版本和审计，唯一标识不能复用。'
        : check.uncertain_tasks ? `有 ${check.uncertain_tasks} 个任务尚不能确认是否仍依赖此 Skill，暂不能删除。`
          : check.blocking_tasks ? `有 ${check.blocking_tasks} 个未结束的任务引用此 Skill，暂不能删除。`
            : '当前状态不允许删除，请重新打开详情。';
  return <div className="mt-4 rounded-lg border border-[var(--s-border-default)] p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p role="status" className="text-xs leading-5 text-[var(--s-text-tertiary)]">{explanation}</p>
      <div className="flex shrink-0 gap-2">
        {!checking && <Button variant="secondary" size="sm" disabled={busy} onClick={() => setAttempt(value => value + 1)}>重新检查</Button>}
        <Button variant="danger" size="sm" disabled={busy || checking || !check?.allowed} onClick={onDelete}>删除 Skill</Button>
      </div>
    </div>
  </div>;
}
