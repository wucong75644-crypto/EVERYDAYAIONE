import { useEffect, useState } from 'react';
import { getOrgDetail, type OrgDetail } from '../../services/org';

export default function OrgInfoSection({ orgId }: { orgId: string }) {
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        setOrg(await getOrgDetail(orgId));
      } catch {
        setOrg(null);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [orgId]);

  if (loading || !org) {
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  return (
    <div className="space-y-3">
      <div className="bg-surface rounded-lg p-4 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">企业名称</span>
          <span className="text-text-primary font-medium">{org.name}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">状态</span>
          <span className={org.status === 'active' ? 'text-success' : 'text-error'}>
            {org.status === 'active' ? '正常运行' : '已停用'}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">企业 ID</span>
          <span className="text-text-disabled text-xs font-mono">{org.id}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">创建时间</span>
          <span className="text-text-secondary">
            {new Date(org.created_at).toLocaleString()}
          </span>
        </div>
      </div>
      <div className="bg-accent-light p-3 rounded-lg">
        <p className="text-xs text-accent font-medium mb-1">企业专属登录链接</p>
        <div className="flex items-center space-x-2">
          <input
            type="text"
            value={`${window.location.origin}/login?org=${orgId}`}
            readOnly
            className="flex-1 px-2 py-1 text-xs bg-surface-card border rounded text-text-tertiary"
          />
          <button
            onClick={() => void navigator.clipboard.writeText(
              `${window.location.origin}/login?org=${orgId}`,
            )}
            className="px-3 py-1 text-xs bg-accent text-text-on-accent rounded"
          >
            复制
          </button>
        </div>
        <p className="text-xs text-accent mt-1">
          将此链接发给员工，员工打开后可扫码登录并自动绑定企业
        </p>
      </div>
    </div>
  );
}
