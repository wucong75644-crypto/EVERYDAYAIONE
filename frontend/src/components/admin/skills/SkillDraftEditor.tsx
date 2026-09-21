import { Check, CircleDot } from 'lucide-react';
import { useState } from 'react';
import type { DraftContent } from '../../../services/skillAdmin';
import { Input, inputVariants } from '../../ui/Input';
import { SkillDocument } from './SkillDocument';
import { contentName } from './presentation';
import { SkillAssetEditor, SkillTemplateEditor } from './SkillAssets';
import { SkillTemplateInput, SkillTemplateNotice } from './SkillTemplateInput';

const lists = [
  ['triggers', '触发提示', '每行一项，描述适合使用这项 Skill 的任务。'],
  ['allowed_tool_names', '允许的工具', '每行一个工具名；只能收窄已有权限，不会授予新权限。'],
  ['actor_user_ids', '限定成员 ID', '每行一个成员 UUID；留空表示不额外限制成员。'],
  ['required_permissions', '必需权限', '每行一个权限名称。'],
  ['required_feature_flags', '必需功能开关', '每行一个功能开关名称。'],
] as const;
const choices = [
  { key: 'conversation_scopes', label: '会话范围', fallback: ['user'], options: [['user', '个人会话'], ['channel', '群组会话']] },
  { key: 'agent_domains', label: '任务领域', fallback: ['general'], options: [['general', '通用'], ['erp', 'ERP']] },
  { key: 'execution_modes', label: '执行场景', fallback: ['interactive'], options: [['interactive', '交互任务'], ['scheduled', '定时任务'], ['preflight', '执行前检查']] },
] as const;

export function SkillDraftEditor({ content, busy, dirty, onChange, onUpload }: {
  content: DraftContent; busy: boolean; dirty: boolean; onChange: (value: DraftContent) => void; onUpload?: (files: File[]) => void;
}) {
  const [preview, setPreview] = useState(false);
  const metadata = content.catalog_metadata;
  const updateMetadata = (key: string, value: unknown) => onChange({ ...content, catalog_metadata: { ...metadata, [key]: value } });
  return <div className="space-y-5 p-5 sm:p-6">
    <Input label="名称" value={contentName(content)} maxLength={200} disabled={busy} placeholder="例如：订单日报"
      onChange={e => updateMetadata('name', e.target.value || null)} />
    <Input label="用途说明" value={content.description} maxLength={2000} disabled={busy} placeholder="一句话说明它能帮助团队完成什么"
      onChange={e => onChange({ ...content, description: e.target.value })} />
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <label htmlFor="skill-body" className="text-sm font-medium">操作说明</label>
        <div className="flex rounded-md bg-[var(--s-surface-sunken)] p-1" aria-label="正文显示方式">
          {[false, true].map(value => <button type="button" key={String(value)} aria-pressed={preview === value} onClick={() => setPreview(value)}
            className={`rounded px-3 py-1 text-xs ${preview === value ? 'bg-[var(--s-surface-raised)] text-[var(--s-text-primary)] shadow-sm' : 'text-[var(--s-text-tertiary)]'}`}>{value ? '预览' : '编辑'}</button>)}
        </div>
      </div>
      <p className="mb-3 text-xs text-[var(--s-text-tertiary)]">直接写清要做什么、按什么步骤做、输出什么格式。附件和动态信息按需添加。</p>
      <SkillTemplateNotice content={content} busy={busy} onChange={onChange} />
      {preview ? <SkillDocument body={content.body} /> : <div className="mt-3"><SkillTemplateInput id="skill-body" label="Skill 操作说明" value={content.body}
        variables={content.template_variables} maxLength={1000000} busy={busy} className="min-h-60 resize-y leading-7"
        onChange={(body, variables) => onChange({ ...content, body, ...(variables ? { template_variables: variables } : {}) })} /></div>}
      <div className="mt-2 flex items-center justify-between gap-3 text-xs text-[var(--s-text-tertiary)]"><span className="flex items-center gap-1.5">{dirty ? <CircleDot size={13} /> : <Check size={13} />}{dirty ? '有未保存的修改' : '草稿已保存'}</span><span>支持 Markdown</span></div>
    </div>
    <SkillAssetEditor content={content} busy={busy} onChange={onChange} onUpload={onUpload} />
    <details className="border-t border-[var(--s-border-default)] pt-4">
      <summary className="cursor-pointer text-sm font-medium text-[var(--s-text-secondary)]">高级设置</summary>
      <div className="mt-4 space-y-4">
        <SkillTemplateEditor content={content} busy={busy} onChange={onChange} />
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={metadata.model_selectable === true} disabled={busy} onChange={e => updateMetadata('model_selectable', e.target.checked)} /><span>允许模型选择此 Skill<span className="mt-1 block text-xs text-[var(--s-text-tertiary)]">默认关闭。发布与组织授权规则仍然适用。</span></span></label>
        {choices.map(group => {
          const selected = Array.isArray(metadata[group.key]) ? metadata[group.key] as string[] : [...group.fallback];
          return <fieldset key={group.key} disabled={busy}><legend className="mb-2 text-sm font-medium">{group.label}</legend><div className="flex flex-wrap gap-4">{group.options.map(([value, label]) => <label key={value} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(value)} onChange={e => updateMetadata(group.key, e.target.checked ? [...selected, value] : selected.filter(v => v !== value))} />{label}</label>)}</div></fieldset>;
        })}
        {lists.map(([key, label, hint]) => <MetadataList key={key} label={label} hint={hint} values={metadata[key]} busy={busy} onChange={values => updateMetadata(key, values)} />)}
      </div>
    </details>
  </div>;
}

// Keep the raw multiline input while typing, including trailing newlines.
function MetadataList({ label, hint, values, busy, onChange }: { label: string; hint: string; values: unknown; busy: boolean; onChange: (value: string[]) => void }) {
  return <label className="block text-sm font-medium">{label}
    <textarea className={`${inputVariants()} mt-2 min-h-20 font-mono`} defaultValue={Array.isArray(values) ? values.join('\n') : ''} disabled={busy}
      onChange={e => onChange(e.target.value.split('\n').map(value => value.trim()).filter(Boolean))} />
    <span className="mt-1 block text-xs font-normal text-[var(--s-text-tertiary)]">{hint}</span>
  </label>;
}
