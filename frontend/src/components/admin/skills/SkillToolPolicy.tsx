import type { DraftContent } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';

export function SkillToolPolicy({ content, busy, onChange }: {
  content: DraftContent; busy: boolean; onChange: (value: DraftContent) => void;
}) {
  const metadata = content.catalog_metadata;
  const platform = metadata.tool_policy === 'platform';
  const tools = Array.isArray(metadata.allowed_tool_names) ? metadata.allowed_tool_names : [];
  return <section aria-label="Skill 工具权限" className="rounded-md border border-[var(--s-border-default)] p-3 text-sm">
    <p className="font-medium">工具权限</p>
    {platform ? <p className="mt-1 text-[var(--s-text-secondary)]">使用当前会话已获准的工具。组织、成员权限和操作审批仍然生效，无需填写工具名称。</p>
      : <>
        <p className="mt-1 text-[var(--s-text-secondary)]">此草稿沿用旧版限制：{tools.length ? `仅可使用已配置的 ${tools.length} 个工具。` : '不能调用工具，只能根据已有内容回答。'}</p>
        <Button type="button" variant="secondary" size="sm" className="mt-2" disabled={busy}
          onClick={() => onChange({ ...content, catalog_metadata: { ...metadata, tool_policy: 'platform', allowed_tool_names: [] } })}>改用当前会话权限</Button>
        <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">修改只对审核发布后的新版本生效，历史版本和已有任务保持原权限。</p>
      </>}
  </section>;
}
