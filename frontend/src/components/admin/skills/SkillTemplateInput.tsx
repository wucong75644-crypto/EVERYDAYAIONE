import { useLayoutEffect, useRef } from 'react';
import type { DraftContent, SkillTemplateVariable } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';
import { inputVariants } from '../../ui/Input';
import { missingTemplateVariables, serverVariable, templateSources } from './templateEditing';

export function SkillTemplateInput({ id, label, value, variables = {}, busy, maxLength, className, onChange }: {
  id: string; label: string; value: string; variables?: DraftContent['template_variables'];
  busy: boolean; maxLength: number; className: string;
  onChange: (value: string, variables?: DraftContent['template_variables']) => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const selection = useRef<{ start: number; end: number } | null>(null);
  const pendingCursor = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (pendingCursor.current === null || !input.current) return;
    input.current.focus();
    input.current.setSelectionRange(pendingCursor.current, pendingCursor.current);
    pendingCursor.current = null;
  }, [value]);
  const canInsert = (source: SkillTemplateVariable['source']) => {
    const existing = variables[source];
    return existing ? existing.source === source && existing.type === serverVariable(source).type : Object.keys(variables).length < 16;
  };
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--s-text-tertiary)]">
      <select aria-label={`${label}：插入动态信息`} value="" disabled={busy} className={`${inputVariants()} !w-auto !py-1.5 !text-xs`}
        onChange={event => {
          const source = templateSources.find(([key]) => key === event.target.value)?.[0];
          if (!source || !canInsert(source)) return;
          const start = Math.min(selection.current?.start ?? value.length, value.length);
          const end = Math.min(selection.current?.end ?? value.length, value.length);
          const token = `{{args.${source}}}`;
          const next = value.slice(0, start) + token + value.slice(end);
          if (next.length > maxLength) return;
          pendingCursor.current = start + token.length;
          selection.current = { start: pendingCursor.current, end: pendingCursor.current };
          onChange(next, { ...variables, [source]: serverVariable(source) });
        }}>
        <option value="" disabled>插入动态信息（可选）</option>
        {templateSources.map(([source, name]) => <option key={source} value={source} disabled={!canInsert(source)}>{name}</option>)}
      </select>
      <span>选中即可插入，使用时自动填写。</span>
    </div>
    <textarea ref={input} id={id} aria-label={label} value={value} maxLength={maxLength} disabled={busy}
      className={`${inputVariants()} ${className}`} spellCheck={false}
      onSelect={event => { selection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }}
      onChange={event => onChange(event.target.value)} />
  </div>;
}

export function SkillTemplateNotice({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (value: DraftContent) => void;
}) {
  const missing = missingTemplateVariables(content);
  if (!missing.length) return null;
  const supported = templateSources.filter(([source]) => missing.includes(source));
  const unknown = missing.filter(name => !supported.some(([source]) => source === name));
  const exceedsLimit = Object.keys(content.template_variables || {}).length + supported.length > 16;
  return <div className="space-y-2 rounded-md border border-[var(--s-border-default)] bg-[var(--s-surface-sunken)] p-3 text-sm" aria-label="需要启用的动态信息">
    {supported.length > 0 && <>
      <p>这份说明使用了：{supported.map(([, label]) => label).join('、')}。点击启用后即可使用。</p>
      <Button variant="secondary" size="sm" disabled={busy || exceedsLimit} onClick={() => onChange({ ...content,
        template_variables: { ...content.template_variables, ...Object.fromEntries(supported.map(([source]) => [source, serverVariable(source)])) },
      })}>启用这些信息</Button>
      {exceedsLimit && <p>动态信息已达到数量上限，请在高级设置中移除不用的配置。</p>}
    </>}
    {unknown.length > 0 && <p className="break-words">无法识别的动态信息：{unknown.join('、')}。请删除相应占位符，再从“插入动态信息”中选择。</p>}
  </div>;
}
