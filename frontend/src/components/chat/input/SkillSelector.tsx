import { useRef, useState } from 'react';
import { BookOpen, Check } from 'lucide-react';
import { Popover } from '../../primitives/Popover';
import { cn } from '../../../utils/cn';
import { getAvailableSkills, skillVersion, type SkillSummary } from '../../../services/skills';

interface Props {
  conversationId: string | null;
  ensureConversation: () => Promise<string>;
  selected: SkillSummary | null;
  onSelect: (skill: SkillSummary | null, conversationId: string) => void;
  disabled: boolean;
}

export default function SkillSelector({ conversationId, ensureConversation, selected, onSelect, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [catalog, setCatalog] = useState<{ conversationId: string; skills: SkillSummary[] } | null>(null);
  const requestId = useRef(0);
  const creatingConversation = useRef<Promise<string> | null>(null);
  const skills = catalog?.conversationId === conversationId ? catalog.skills : [];

  const refresh = async () => {
    const id = ++requestId.current;
    setLoading(true);
    setFailed(false);
    setCatalog(null);
    try {
      if (!conversationId && !creatingConversation.current) {
        creatingConversation.current = ensureConversation().finally(() => { creatingConversation.current = null; });
      }
      const currentId = conversationId ?? await creatingConversation.current!;
      const result = await getAvailableSkills(currentId);
      if (id !== requestId.current) return;
      setCatalog({ conversationId: currentId, skills: result });
    } catch {
      if (id === requestId.current) setFailed(true);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  };

  const choose = (skill: SkillSummary | null) => {
    if (!conversationId || disabled) return;
    onSelect(skill, conversationId);
    setOpen(false);
  };

  return <Popover side="top" align="start" className="!p-2 w-72" maxWidth={288}
    open={open && !disabled} onOpenChange={(next) => {
      setOpen(next);
      if (next) void refresh();
    }}
    trigger={<button type="button" disabled={disabled} aria-label={selected ? `Skill：${selected.name}` : '选择 Skill'}
      title={selected ? `${selected.name} · ${skillVersion(selected.revision)} · 仅本条消息` : '选择 Skill · 仅本条消息'}
      className={cn('flex items-center gap-1 p-2 rounded-lg text-sm transition-base disabled:opacity-40',
        selected ? 'bg-accent-light text-accent' : 'text-text-tertiary hover:text-text-primary hover:bg-hover')}>
      <BookOpen className="w-4 h-4 shrink-0" />
      <span className="hidden sm:inline max-w-24 truncate">{selected?.name ?? 'Skill'}</span>
      {selected && <Check className="w-3 h-3 shrink-0" />}
    </button>}>
    <div className="px-2 py-1 text-xs text-text-tertiary">选择 Skill · 仅本条消息</div>
    <button type="button" onClick={() => choose(null)}
      className="w-full px-2 py-2 text-left text-sm rounded-lg hover:bg-hover text-text-secondary">不手动选择</button>
    {loading ? <p role="status" className="p-2 text-sm text-text-tertiary">正在加载可用 Skill…</p>
      : failed ? <div className="p-2 text-sm text-text-tertiary">
        <p role="status">暂时无法获取 Skill，请重试。</p>
        <button type="button" onClick={() => void refresh()} className="mt-2 text-accent">重试</button>
      </div>
      : skills.length === 0 ? <p role="status" className="p-2 text-sm text-text-tertiary">当前会话暂无可用 Skill</p>
      : <div className="max-h-64 overflow-y-auto">
        {skills.map((skill) => <button type="button" key={skill.skill_id} onClick={() => choose(skill)}
          aria-pressed={selected?.skill_id === skill.skill_id && selected.revision === skill.revision}
          className="w-full p-2 text-left rounded-lg hover:bg-hover">
          <div className="text-sm text-text-primary break-words">{skill.name} · {skillVersion(skill.revision)}</div>
          <div className="mt-1 text-xs text-text-tertiary line-clamp-2">{skill.description}</div>
        </button>)}
      </div>}
  </Popover>;
}
