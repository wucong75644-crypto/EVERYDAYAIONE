import { useRef, useState } from 'react';
import { BookOpen, Check, ChevronDown, MessageSquare, Pin, X } from 'lucide-react';
import { Popover } from '../../primitives/Popover';
import { skillVersion, supportsSkillMode, type SkillTaskMode, type SkillBinding, type SkillSummary } from '../../../services/skills';
import type { SkillBindingsState } from './useSkillBindings';

interface Props {
  selected: SkillSummary | null;
  state: SkillBindingsState;
  disabled: boolean;
  onSelect: (skill: SkillSummary | null) => void;
  onComplete: () => void;
  inline?: boolean;
  taskMode?: SkillTaskMode;
}
function SkillTag({ skill, binding, selected, state, disabled, onSelect, onComplete }: Props & {
  skill: SkillSummary; binding?: SkillBinding;
}) {
  const [open, setOpen] = useState(false);
  const focusInput = useRef(false);
  const locked = disabled || state.blocked;
  const scope = binding ? '会话固定' : '仅本条';
  const finish = (choice?: SkillSummary | null) => {
    focusInput.current = true;
    if (choice !== undefined) onSelect(choice);
    setOpen(false);
    onComplete();
  };
  return <div data-skill-tag role="group" aria-label={binding ? '会话固定的 Skill' : '已选择的 Skill'}
      title={`${skill.name} · ${skillVersion(skill.revision)} · ${scope}`}
      className="flex h-6 max-w-full items-center gap-1 rounded-lg border border-border-default bg-hover px-1.5 text-[13px] leading-5 text-text-secondary">
    <BookOpen className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
    <span className="min-w-0 truncate">{skill.name}</span>
    {binding && !binding.available && <span className="shrink-0 text-xs text-error">（{binding.capability_status?.some(item => item.required && !item.available) ? '依赖能力不可用' : '不可用'}）</span>}
    <Popover side="top" align="start" maxWidth={288} className="!p-2 !bg-surface-card !border-border-default w-[min(288px,calc(100vw-16px))]"
      open={open && !disabled} onOpenChange={(next) => { focusInput.current = false; setOpen(next); }}
      onCloseAutoFocus={(event) => { if (focusInput.current) { event.preventDefault(); onComplete(); } }}
      trigger={<button type="button" disabled={locked} aria-label={`使用范围：${skill.name}，${scope}`}
        title={scope}
        className={`flex shrink-0 items-center gap-0.5 rounded border-l border-border-default p-0.5 hover:bg-accent-light focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40 ${binding ? 'text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]' : 'text-text-tertiary hover:text-text-primary'}`}>
        {binding ? <Pin className="h-3 w-3" aria-hidden="true" /> : <MessageSquare className="h-3 w-3" aria-hidden="true" />}
        <ChevronDown className="h-2.5 w-2.5" aria-hidden="true" />
      </button>}>
      <p className="px-2 py-1 text-xs text-text-tertiary">使用范围 · {skillVersion(skill.revision)}</p>
      <button type="button" aria-pressed={!binding} disabled={locked || (!!binding && (!state.ready || !binding.available))}
        onClick={() => binding ? void state.remove(binding, () => finish(skill), true) : finish()}
        className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-hover disabled:opacity-40">
        <MessageSquare className="h-4 w-4 shrink-0 text-text-tertiary" />
        <div className="flex-1"><p className="text-sm text-text-primary">仅本条消息</p><p className="mt-0.5 text-xs text-text-tertiary">随下一条消息使用，发送后清除</p></div>
        {!binding && <Check className="h-4 w-4 text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]" />}
      </button>
      <button type="button" aria-pressed={!!binding}
        disabled={locked || (!binding && (!state.ready || state.bindings.length >= 4 || state.bindings.some(b => b.skill_id === skill.skill_id)))}
        onClick={() => binding ? finish() : void state.pin(skill, () => finish(null))}
        className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-hover disabled:opacity-40">
        <Pin className="h-4 w-4 shrink-0 text-text-tertiary" />
        <div className="flex-1"><p className="text-sm text-text-primary">固定到当前会话</p><p className="mt-0.5 text-xs text-text-tertiary">后续消息持续使用，直到移除</p></div>
        {binding && <Check className="h-4 w-4 text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)]" />}
      </button>
      <p className="px-2 pt-2 text-xs leading-5 text-text-tertiary">最多固定 4 个；只影响新消息，计划任务不继承。旧版本需移除后重新选择。</p>
    </Popover>
    <button type="button" disabled={locked || (!!binding && !state.ready)}
      onClick={() => binding
        ? void state.remove(binding, () => finish(selected?.skill_id === binding.skill_id ? null : undefined))
        : finish(null)}
      aria-label={binding ? `移除会话 Skill：${skill.name}` : `取消 Skill：${skill.name}`} title="移除 Skill"
      className="shrink-0 rounded p-0.5 text-text-tertiary hover:bg-hover hover:text-text-primary focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-40">
      <X className="h-3.5 w-3.5" aria-hidden="true" />
    </button>
  </div>;
}

export default function SessionSkillBindings(props: Props) {
  const { selected, state, disabled, inline = false, taskMode = 'smart' } = props;
  const tags: { skill: SkillSummary; binding?: SkillBinding }[] = state.bindings.filter(b => supportsSkillMode(b, taskMode)).map(binding => ({ skill: binding, binding }));
  if (selected && !state.bindings.some(b => b.skill_id === selected.skill_id && b.revision === selected.revision)) tags.push({ skill: selected });
  if (!tags.length && (inline || (!state.error && !state.pending))) return null;
  return <div aria-label="Skill 标签与使用范围" className={inline ? 'flex w-fit max-w-full flex-wrap items-center gap-1' : 'mb-2 flex flex-wrap items-center gap-2 pt-1'}>
    {tags.map(({ skill, binding }) => <SkillTag key={`${skill.skill_id}:${skill.revision}`} {...props} skill={skill} binding={binding} />)}
    {!inline && <SkillBindingFeedback state={state} disabled={disabled} />}
  </div>;
}

export function SkillBindingFeedback({ state, disabled }: Pick<Props, 'state' | 'disabled'>) {
  return <>
    {state.pending && <p role="status" className="basis-full text-xs text-text-tertiary">正在保存使用范围…</p>}
    {state.error && <div role="alert" className="basis-full text-xs text-text-secondary">
      {state.error}<button type="button" disabled={disabled || state.pending || state.loading}
        onClick={() => void state.refresh()} className="ml-2 text-accent dark:text-[color-mix(in_srgb,var(--color-accent),white_45%)] disabled:opacity-40">刷新重试</button>
    </div>}
  </>;
}
