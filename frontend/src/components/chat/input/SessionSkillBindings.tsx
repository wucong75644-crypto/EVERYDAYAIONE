import { useCallback, useEffect, useRef, useState } from 'react';
import {
  addSkillBinding, getAvailableSkills, getSkillBindings, removeSkillBinding, skillVersion,
  type SkillBinding, type SkillSummary,
} from '../../../services/skills';

export default function SessionSkillBindings({ conversationId, disabled }: {
  conversationId: string; disabled: boolean;
}) {
  const [data, setData] = useState<{ bindings: SkillBinding[]; catalog: SkillSummary[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const requests = useRef({ load: 0, mutation: 0 });

  const refresh = useCallback(async () => {
    const current = ++requests.current.load;
    setLoading(true);
    setError('');
    try {
      const [bindings, catalog] = await Promise.all([
        getSkillBindings(conversationId), getAvailableSkills(conversationId),
      ]);
      if (current === requests.current.load) setData({ bindings, catalog });
    } catch {
      if (current === requests.current.load) {
        setData(null);
        setError('暂时无法获取会话 Skill，请重试。');
      }
    } finally {
      if (current === requests.current.load) setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    const lifecycle = requests.current;
    void refresh();
    return () => { lifecycle.load++; lifecycle.mutation++; };
  }, [refresh]);

  const mutate = async (operation: () => Promise<unknown>) => {
    if (disabled || pending || loading) return;
    const current = requests.current.load;
    const mutation = ++requests.current.mutation;
    setPending(true);
    setError('');
    try {
      await operation();
      if (current === requests.current.load) await refresh();
    } catch {
      if (current === requests.current.load) setError('修改未成功，请刷新后重试；Skill 版本或权限可能已变化。');
    } finally {
      if (mutation === requests.current.mutation) setPending(false);
    }
  };

  const locked = disabled || pending || loading;
  const bound = new Set(data?.bindings.map(skill => skill.skill_id));
  return <section aria-label="当前会话固定 Skill" className="px-2 py-1">
    <p className="text-xs text-text-tertiary mb-2">
      每条新消息自动启用固定版本。添加或移除只影响新任务，已开始的任务保持原设置。计划任务不继承。
    </p>
    {loading && <p role="status" className="text-sm text-text-tertiary">正在加载会话 Skill…</p>}
    {error && <div role="alert" className="text-sm text-text-secondary">
      <p>{error}</p>
      <button type="button" disabled={pending || loading} onClick={() => void refresh()}
        className="py-2 text-accent">刷新重试</button>
    </div>}
    {!loading && data && <div className="max-h-72 overflow-y-auto">
      {data.bindings.length === 0 && <p className="py-2 text-sm text-text-tertiary">尚未固定 Skill</p>}
      {data.bindings.map(skill => <div key={skill.binding_id} className="py-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-text-primary break-words">{skill.name} · {skillVersion(skill.revision)}</p>
          <p className="text-xs text-text-tertiary">{skill.available ? '版本已固定' : '当前不可用，请移除或联系管理员；新任务将停止'}</p>
        </div>
        <button type="button" disabled={locked} aria-label={`移除会话 Skill：${skill.name}`}
          onClick={() => void mutate(() => removeSkillBinding(conversationId, skill.binding_id))}
          className="text-sm text-text-secondary hover:text-text-primary disabled:opacity-40">移除</button>
      </div>)}
      <div className="mt-2 border-t border-border pt-2 text-xs text-text-tertiary">添加固定 Skill（最多 4 个）</div>
      {data.catalog.filter(skill => !bound.has(skill.skill_id)).map(skill => <div key={skill.skill_id}
        className="py-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-text-primary break-words">{skill.name} · {skillVersion(skill.revision)}</p>
          <p className="text-xs text-text-tertiary line-clamp-2">{skill.description}</p>
        </div>
        <button type="button" disabled={locked || data.bindings.length >= 4}
          aria-label={`固定 Skill：${skill.name}`}
          onClick={() => void mutate(() => addSkillBinding(conversationId, skill))}
          className="text-sm text-accent disabled:opacity-40">固定</button>
      </div>)}
      {data.catalog.length === 0 && <p className="py-2 text-sm text-text-tertiary">暂无可添加的 Skill</p>}
    </div>}
  </section>;
}
