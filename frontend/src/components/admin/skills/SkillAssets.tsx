import { useRef, useState } from 'react';
import type { DraftContent, SkillAssetDraft, SkillAssetSummary } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';
import { Input, inputVariants } from '../../ui/Input';
import { SkillTemplateInput } from './SkillTemplateInput';
import { serverVariable, templateSources } from './templateEditing';

const kinds = { reference: '只读参考资料', template: '模板', example_input: '示例输入', example_output: '示例输出' };

export function SkillTemplateEditor({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (content: DraftContent) => void;
}) {
  return <fieldset disabled={busy} className="space-y-2"><legend className="mb-2 text-sm font-medium">服务端模板变量</legend>
    <p className="text-xs text-[var(--s-text-tertiary)]">通常无需在此配置。在编辑区选择“插入动态信息”会同时启用对应信息。</p>
    {templateSources.map(([source, label]) => <label key={source} className="flex flex-wrap items-center gap-2 text-sm">
      <input type="checkbox" checked={!!content.template_variables?.[source]} onChange={e => {
        const variables = { ...content.template_variables };
        if (e.target.checked) variables[source] = serverVariable(source);
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
      <p className="font-medium">{asset.name}<span className="ml-2 text-xs font-normal text-[var(--s-text-tertiary)]">{kinds[asset.kind]} · {(asset.file_format || asset.format).toUpperCase()}{(asset.file_bytes ?? asset.bytes) !== undefined && ` · ${asset.file_bytes ?? asset.bytes} 字节`}</span></p>
      <p className="mt-1 text-[var(--s-text-secondary)]">{asset.summary}</p>
    </li>)}</ul>
    <p className="mt-3 text-xs text-[var(--s-text-tertiary)]">仅在操作说明明确引用且容量允许时提供附件内容。</p>
  </section>;
}

export function SkillAssetEditor({ content, busy, onChange, onUpload }: {
  content: DraftContent; busy: boolean; onChange: (content: DraftContent) => void; onUpload?: (files: File[]) => void;
}) {
  const assets = content.assets || [];
  const fileInput = useRef<HTMLInputElement>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const update = (index: number, patch: Partial<SkillAssetDraft>, variables?: DraftContent['template_variables']) => onChange({ ...content,
    ...(variables ? { template_variables: variables } : {}),
    assets: assets.map((asset, i) => i === index ? { ...asset, ...patch } : asset) });
  return <section className="space-y-3 border-t border-[var(--s-border-default)] pt-4" aria-label="编辑附件">
    <h3 className="text-sm font-medium">附件与模板（可选）</h3>
    <p className="text-xs text-[var(--s-text-tertiary)]">直接上传参考资料、模板或示例文件，无需填写附件内容。需要 Skill 使用时，点击“在操作说明中引用”。</p>
    <input ref={fileInput} type="file" multiple hidden aria-label="选择附件文件" disabled={busy || assets.length >= 16 || !onUpload}
      accept=".docx,.pdf,.xlsx,.txt,.md,.csv,.json" onChange={event => {
        const files = Array.from(event.target.files || []);
        event.target.value = '';
        if (files.length && !busy) onUpload?.(files);
      }} />
    <Button variant="secondary" size="sm" disabled={busy || assets.length >= 16 || !onUpload} onClick={() => fileInput.current?.click()}>上传附件</Button>
    <p className="text-xs text-[var(--s-text-tertiary)]">支持 Word（DOCX）、PDF、Excel（XLSX）、TXT、Markdown、CSV、JSON。每份最多 2 MB，共 8 MB，最多 16 份；读取文字每份最多 64 KiB。扫描版 PDF 暂不支持。</p>
    <ul className="space-y-3">{assets.map((asset, index) => <li key={asset.id} className="rounded-md border border-[var(--s-border-default)] p-3">
      <p className="break-all text-sm font-medium">{asset.name || `附件 ${index + 1}`}</p>
      <p className="mt-1 text-xs text-[var(--s-text-tertiary)]">{(asset.source?.format || asset.format).toUpperCase()} · {kinds[asset.kind]}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" disabled={busy || content.body.includes(`[[asset:${asset.id}]]`)} onClick={() => onChange({ ...content,
          body: `${content.body}${content.body ? '\n\n' : ''}请参考附件 [[asset:${asset.id}]]。`,
        })}>{content.body.includes(`[[asset:${asset.id}]]`) ? '已在操作说明中引用' : '在操作说明中引用'}</Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => {
          const marker = `[[asset:${asset.id}]]`;
          onChange({ ...content, assets: assets.filter((_, i) => i !== index),
            body: content.body.replaceAll(`请参考附件 ${marker}。`, '').replaceAll(marker, '').trimEnd() });
        }}>移除附件</Button>
      </div>
      <details open={expanded === asset.id} className="mt-2">
      <summary className="cursor-pointer text-xs text-[var(--s-text-tertiary)]" onClick={event => { event.preventDefault(); setExpanded(expanded === asset.id ? null : asset.id); }}>查看内容与设置</summary>
      <fieldset disabled={busy} className="mt-3 space-y-3">
        <Input label="附件名称" value={asset.name} maxLength={120} placeholder="例如：日报格式" onChange={e => update(index, { name: e.target.value })} />
        <Input label="附件说明（选填）" value={asset.summary} maxLength={500} placeholder="例如：规定日报的标题和内容顺序" onChange={e => update(index, { summary: e.target.value || `参考资料：${asset.name}` })} />
        <label className="block text-sm">附件类型<select className={`${inputVariants()} mt-1`} value={asset.kind} onChange={e => update(index, { kind: e.target.value as SkillAssetDraft['kind'] })}>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        {asset.source ? <label className="block text-sm">读取内容（预览）<textarea readOnly className={`${inputVariants()} mt-1 min-h-40`} value={asset.content} /></label>
          : asset.kind === 'template' ? <div><label htmlFor={`skill-asset-${asset.id}`} className="mb-2 block text-sm">附件内容</label>
          <SkillTemplateInput id={`skill-asset-${asset.id}`} label="附件内容" value={asset.content} variables={content.template_variables}
            busy={busy} maxLength={65536} className="min-h-40 leading-7" onChange={(text, variables) => update(index, { content: text }, variables)} /></div>
          : <label className="block text-sm">附件内容<textarea className={`${inputVariants()} mt-1 min-h-40`} value={asset.content} maxLength={65536} onChange={e => update(index, { content: e.target.value })} /></label>}
        <p className="text-xs text-[var(--s-text-tertiary)]">附件随版本审核发布。修改文件内容时，请移除后重新上传。</p>
      </fieldset>
      </details>
    </li>)}</ul>
  </section>;
}
