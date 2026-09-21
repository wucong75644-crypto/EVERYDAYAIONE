import type { DraftContent, SkillAssetDraft, SkillAssetSummary, SkillTemplateVariable } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';
import { Input, inputVariants } from '../../ui/Input';

const kinds = { reference: '只读参考资料', template: '模板', example_input: '示例输入', example_output: '示例输出' };

const sources: [SkillTemplateVariable['source'], string][] = [
  ['org_id', '当前组织 ID'], ['actor_user_id', '当前成员 ID'], ['conversation_scope', '会话范围'],
  ['agent_domain', '任务领域'], ['execution_mode', '执行场景'], ['is_channel', '是否群组会话'],
];

export function SkillTemplateEditor({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (content: DraftContent) => void;
}) {
  return <fieldset disabled={busy} className="space-y-2"><legend className="mb-2 text-sm font-medium">服务端模板变量</legend>
    <p className="text-xs text-[var(--s-text-tertiary)]">选中后可在正文或模板附件中使用相应变量，值由当前任务提供。</p>
    {sources.map(([source, label]) => <label key={source} className="flex flex-wrap items-center gap-2 text-sm">
      <input type="checkbox" checked={!!content.template_variables?.[source]} onChange={e => {
        const variables = { ...content.template_variables };
        if (e.target.checked) variables[source] = { source, type: source === 'is_channel' ? 'boolean' : 'string' };
        else delete variables[source];
        onChange({ ...content, template_variables: variables });
      }} />{label}<code className="text-xs text-[var(--s-text-tertiary)]">{`{{args.${source}}}`}</code><span className="text-xs">{source === 'is_channel' ? '布尔值' : '文本'}</span>
    </label>)}
  </fieldset>;
}

export function SkillAssets({ assets }: { assets: SkillAssetSummary[] }) {
  if (!assets.length) return null;
  return <section className="mt-5 border-t border-[var(--s-border-default)] pt-4" aria-label="附件摘要">
    <h3 className="text-sm font-medium">附件与模板（{assets.length}）</h3>
    <ul className="mt-3 space-y-3">{assets.map(asset => <li key={asset.id} className="text-sm">
      <p className="font-medium">{asset.name}<span className="ml-2 text-xs font-normal text-[var(--s-text-tertiary)]">{kinds[asset.kind]} · {asset.format.toUpperCase()}{asset.bytes !== undefined && ` · ${asset.bytes} 字节`}</span></p>
      <p className="mt-1 text-[var(--s-text-secondary)]">{asset.summary}</p>
    </li>)}</ul>
    <p className="mt-3 text-xs text-[var(--s-text-tertiary)]">仅在操作说明明确引用且容量允许时提供附件内容。</p>
  </section>;
}

export function SkillAssetEditor({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (content: DraftContent) => void;
}) {
  const assets = content.assets || [];
  const update = (index: number, patch: Partial<SkillAssetDraft>) => onChange({ ...content,
    assets: assets.map((asset, i) => i === index ? { ...asset, ...patch } : asset) });
  return <section className="space-y-3 border-t border-[var(--s-border-default)] pt-4" aria-label="编辑附件">
    <h3 className="text-sm font-medium">附件与模板</h3>
    <p className="text-xs text-[var(--s-text-tertiary)]">支持 Markdown、纯文本、JSON 和 CSV，每份最多 64 KiB、共 256 KiB。附件随版本审核发布。</p>
    {assets.map((asset, index) => <details key={index} className="rounded-md border border-[var(--s-border-default)] p-3">
      <summary className="cursor-pointer text-sm">{asset.name || `附件 ${index + 1}`} · {kinds[asset.kind]}</summary>
      <fieldset disabled={busy} className="mt-3 space-y-3">
        <Input label="附件标识" value={asset.id} maxLength={64} placeholder="例如：report-template" onChange={e => update(index, { id: e.target.value })} />
        <Input label="附件名称" value={asset.name} maxLength={120} onChange={e => update(index, { name: e.target.value })} />
        <Input label="附件用途" value={asset.summary} maxLength={500} onChange={e => update(index, { summary: e.target.value })} />
        <label className="block text-sm">附件类型<select className={`${inputVariants()} mt-1`} value={asset.kind} onChange={e => update(index, { kind: e.target.value as SkillAssetDraft['kind'] })}>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="block text-sm">文本格式<select className={`${inputVariants()} mt-1`} value={asset.format} onChange={e => update(index, { format: e.target.value as SkillAssetDraft['format'] })}>{['md', 'txt', 'json', 'csv'].map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}</select></label>
        <label className="block text-sm">附件内容<textarea className={`${inputVariants()} mt-1 min-h-40 font-mono`} value={asset.content} maxLength={65536} onChange={e => update(index, { content: e.target.value })} /></label>
        <p className="text-xs text-[var(--s-text-tertiary)]">在操作说明中引用：<code>{`[[asset:${asset.id || '附件标识'}]]`}</code>。模板变量使用高级设置中声明的服务端值。</p>
        <Button variant="secondary" size="sm" onClick={() => onChange({ ...content, assets: assets.filter((_, i) => i !== index) })}>移除附件</Button>
      </fieldset>
    </details>)}
    <Button variant="secondary" size="sm" disabled={busy || assets.length >= 16} onClick={() => onChange({ ...content,
      assets: [...assets, { id: '', name: '', kind: 'reference', summary: '', format: 'md', content: '' }] })}>添加附件</Button>
  </section>;
}
