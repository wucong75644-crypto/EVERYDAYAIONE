import type { Organization } from '../../types/auth';

export default function LoginOrganizationChoice({ organizations, onChoose }: {
  organizations: Organization[];
  onChoose: (organization: Organization | null) => void;
}) {
  return <div role="group" aria-label="选择登录空间" className="space-y-2">
    <p className="text-sm text-text-secondary">无法自动恢复企业选择，请选择登录空间。企业资料与个人空间相互独立。</p>
    {organizations.map(org => <button key={org.org_id} type="button" onClick={() => onChoose(org)}
      className="w-full rounded-lg border px-3 py-2">进入 {org.name}</button>)}
    <button type="button" onClick={() => onChoose(null)} className="w-full rounded-lg border px-3 py-2">以个人身份继续</button>
  </div>;
}
