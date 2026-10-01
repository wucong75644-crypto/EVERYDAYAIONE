import { useRef, useState } from 'react';
import { BookOpen, X } from 'lucide-react';
import { Popover } from '../../primitives/Popover';
import { getAvailableSkills, skillVersion, supportsSkillMode, type SkillTaskMode, type SkillBinding, type SkillSummary } from '../../../services/skills';
import SkillRecommendations from './SkillRecommendations';
import { isSkillRecommendationsUiEnabled } from '../../../config/featureFlags';
import { cn } from '../../../utils/cn';

export interface SkillSelectorProps {
  conversationId: string | null;
  ensureConversation: () => Promise<string>;
  selected: SkillSummary | null;
  onSelect: (skill: SkillSummary | null, conversationId: string) => void;
  disabled: boolean;
  taskMode?: SkillTaskMode;
  permissionMode?: 'auto' | 'ask' | 'plan';
  onSelectionComplete?: () => void;
  bindings?: SkillBinding[];
}

export default function SkillSelector({ conversationId, ensureConversation, selected, onSelect, disabled, onSelectionComplete, permissionMode = 'auto', taskMode = 'smart', bindings = [] }: SkillSelectorProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [catalog, setCatalog] = useState<{ conversationId: string; taskMode: SkillTaskMode; skills: SkillSummary[] } | null>(null);
  const requestId = useRef(0);
  const creatingConversation = useRef<Promise<string> | null>(null);
  const returnToInput = useRef(false);
  const skills = catalog?.conversationId === conversationId && catalog.taskMode === taskMode ? catalog.skills.filter(s => supportsSkillMode(s, taskMode)) : [];

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
      const result = await getAvailableSkills(currentId, ...(taskMode !== 'smart' ? [taskMode] as const : []));
      if (id !== requestId.current) return;
      setCatalog({ conversationId: currentId, taskMode, skills: result });
    } catch {
      if (id === requestId.current) setFailed(true);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  };

  const choose = (skill: SkillSummary | null) => {
    if (!conversationId || disabled) return;
    onSelect(skill, conversationId);
    returnToInput.current = true;
    setOpen(false);
  };

  return <Popover side="top" align="start" className="!p-2 !bg-surface-card !border-border-default w-[min(380px,calc(100vw-16px))]" maxWidth={380}
    open={open && !disabled} onOpenChange={(next) => {
      setOpen(next);
      if (next) {
        returnToInput.current = false;
        void refresh();
      }
    }}
    onCloseAutoFocus={(event) => {
      if (returnToInput.current && onSelectionComplete) {
        event.preventDefault();
        onSelectionComplete();
      }
      returnToInput.current = false;
    }}
    trigger={<button type="button" disabled={disabled} aria-label={selected ? `Skill：${selected.name}` : '选择 Skill'}
      title={selected ? `${selected.name} · ${skillVersion(selected.revision)} · 仅本条消息` : '选择 Skill'}
      className={cn('flex items-center gap-1 p-2 rounded-lg text-sm transition-base disabled:opacity-40',
        selected || bindings.length ? 'bg-accent-light text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)] hover:bg-accent-light/80' : 'text-text-tertiary hover:text-text-primary hover:bg-hover')}>
      <BookOpen className="w-4 h-4 shrink-0" />
      <span className="hidden sm:inline">Skill</span>
    </button>}>
    {loading || failed || !conversationId ? <>
      <div className="flex items-center justify-between px-2 py-1 text-sm font-medium text-text-primary">Skill
        <button type="button" aria-label="关闭 Skill 面板" onClick={() => setOpen(false)} className="rounded-md p-1 text-text-tertiary hover:bg-hover"><X className="h-4 w-4" /></button>
      </div>
      {failed ? <div className="p-3 text-sm text-text-tertiary">
        <p role="status">暂时无法获取 Skill，请重试。</p>
        <button type="button" onClick={() => void refresh()} className="mt-2 text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]">重试</button>
      </div> : <p role="status" className="p-3 text-sm text-text-tertiary">正在加载可用 Skill…</p>}
    </> : <SkillRecommendations key={`${conversationId}:${permissionMode}:${taskMode}`} conversationId={conversationId}
      skills={skills} taskMode={taskMode} disabled={disabled} onSelect={choose} permissionMode={permissionMode} selected={selected}
      bindings={bindings} enabled={isSkillRecommendationsUiEnabled()} onClose={() => setOpen(false)} />}
  </Popover>;
}
