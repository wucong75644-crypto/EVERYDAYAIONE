import { useRef, useState } from 'react';
import { BookOpen } from 'lucide-react';
import { Popover } from '../../primitives/Popover';
import { getAvailableSkills, skillVersion, type SkillSummary } from '../../../services/skills';
import SkillRecommendations from './SkillRecommendations';
import { isSkillRecommendationsUiEnabled } from '../../../config/featureFlags';
import SessionSkillBindings from './SessionSkillBindings';

export interface SkillSelectorProps {
  conversationId: string | null;
  ensureConversation: () => Promise<string>;
  selected: SkillSummary | null;
  onSelect: (skill: SkillSummary | null, conversationId: string) => void;
  disabled: boolean;
  permissionMode?: 'auto' | 'ask' | 'plan';
  onSelectionComplete?: () => void;
}

export default function SkillSelector({ conversationId, ensureConversation, selected, onSelect, disabled, onSelectionComplete, permissionMode = 'auto' }: SkillSelectorProps) {
  const [open, setOpen] = useState(false);
  const [sessionMode, setSessionMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [catalog, setCatalog] = useState<{ conversationId: string; skills: SkillSummary[] } | null>(null);
  const requestId = useRef(0);
  const creatingConversation = useRef<Promise<string> | null>(null);
  const returnToInput = useRef(false);
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
    returnToInput.current = true;
    setOpen(false);
  };

  return <Popover side="top" align="start" className="!p-2 w-72" maxWidth={288}
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
      title={selected ? `${selected.name} · ${skillVersion(selected.revision)} · 仅本条消息` : '选择 Skill 或管理会话固定 Skill'}
      className="flex items-center gap-1 p-2 rounded-lg text-sm transition-base disabled:opacity-40 text-text-tertiary hover:text-text-primary hover:bg-hover">
      <BookOpen className="w-4 h-4 shrink-0" />
      <span className="hidden sm:inline">Skill</span>
    </button>}>
    <div className="flex gap-1 p-1 mb-1">
      <button type="button" aria-pressed={!sessionMode} onClick={() => setSessionMode(false)}
        className="flex-1 rounded-lg p-2 text-xs text-text-secondary hover:bg-hover">仅本条消息</button>
      <button type="button" aria-pressed={sessionMode} onClick={() => setSessionMode(true)}
        className="flex-1 rounded-lg p-2 text-xs text-text-secondary hover:bg-hover">固定到当前会话</button>
    </div>
    {sessionMode ? conversationId
      ? <SessionSkillBindings key={conversationId} conversationId={conversationId} disabled={disabled} />
      : <div className="p-2 text-sm text-text-tertiary">
        <p role="status">{failed ? '暂时无法创建会话，请重试。' : '请稍候，正在创建会话…'}</p>
        {failed && <button type="button" onClick={() => void refresh()} className="mt-2 text-accent">重试</button>}
      </div>
      : <>
    {open && !loading && !failed && conversationId && isSkillRecommendationsUiEnabled() &&
      <SkillRecommendations key={`${conversationId}:${permissionMode}`} conversationId={conversationId}
        skills={skills} disabled={disabled} onSelect={choose} permissionMode={permissionMode} />}
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
    </>}
  </Popover>;
}
