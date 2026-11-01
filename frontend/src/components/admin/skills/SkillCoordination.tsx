import type { DraftContent } from '../../../services/skillAdmin';
import { inputVariants } from '../../ui/Input';

const profiles = {
  guidance: { version: 1, kind: 'guidance', claims: [] },
  inline: { version: 1, kind: 'workflow', claims: [
    { scope: 'image.planning', method: 'ecommerce.inline', actions: ['plan'] },
    { scope: 'image.submission', method: 'image.direct', actions: ['execute_prompt', 'edit'] },
  ] },
  server: { version: 1, kind: 'workflow', claims: [
    { scope: 'image.planning', method: 'ecommerce.server_plan', actions: ['plan'] },
    { scope: 'image.submission', method: 'image.plan_source', actions: ['execute_ready'] },
  ] },
  prompt: { version: 1, kind: 'workflow', claims: [
    { scope: 'image.prompting', method: 'image.prompt', actions: ['plan'] },
  ] },
} as const;

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([key, item]) => !(key === 'requires' && Array.isArray(item) && item.length === 0))
    .sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}

export function SkillCoordination({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (content: DraftContent) => void;
}) {
  const saved = content.catalog_metadata.coordination;
  // Unknown future/custom contracts are preserved until explicitly changed.
  const selected = Object.entries(profiles).find(([, value]) =>
    JSON.stringify(canonical(value)) === JSON.stringify(canonical(saved)))?.[0] ?? (saved ? 'custom' : 'legacy');
  return <section aria-label="Skill 协作方式" className="rounded-md border border-[var(--s-border-default)] p-3 text-sm">
    <label className="font-medium" htmlFor="skill-coordination">协作方式</label>
    <select id="skill-coordination" className={`${inputVariants()} mt-2`} value={selected} disabled={busy}
      onChange={event => {
        const metadata = { ...content.catalog_metadata };
        if (event.target.value === 'legacy') delete metadata.coordination;
        else if (event.target.value in profiles) metadata.coordination = profiles[event.target.value as keyof typeof profiles];
        onChange({ ...content, catalog_metadata: metadata });
      }}>
      <option value="legacy">保持原有方法（未声明）</option>
      {selected === 'custom' && <option value="custom">保留已配置的协作规则</option>}
      <option value="guidance">提供知识、格式或风格建议</option>
      <option value="inline">在聊天中策划图片，按最终提示词生成</option>
      <option value="server">使用服务器电商策划方案生成</option>
      <option value="prompt">整理参考图并输出提示词</option>
    </select>
    <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">用于检查方法是否适合共同执行，不增加工具权限。只对发布后的新版本生效，原版本及任务保持不变。</p>
  </section>;
}
