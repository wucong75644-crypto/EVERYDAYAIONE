import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import {
  createCredential,
  deleteCredential,
  listCredentials,
  testCredential,
  triggerSync,
  type Credential,
  type KuaimaiSource,
} from '../../services/kuaimaiExternal';
import Modal from '../common/Modal';

export default function KuaimaiSourcesTab() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalSource, setModalSource] = useState<KuaimaiSource | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      setCredentials(await listCredentials());
    } catch (error) {
      toast.error(`加载凭证失败: ${(error as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  if (loading) {
    return <div className="text-center py-8 text-[var(--s-text-secondary)]">加载中...</div>;
  }

  return (
    <div className="space-y-4">
      <CredentialCard
        source="thinktank"
        label="📊 智库利润报表"
        description="erp.superboss.cc/kmzk — 收入/退款/成本/利润全套财务核算（T+1）"
        credential={credentials.find((item) => item.source === 'thinktank')}
        onConfigure={() => setModalSource('thinktank')}
        onRefresh={refresh}
      />
      <CredentialCard
        source="viperp"
        label="📈 销售主题报表"
        description="erp.superboss.cc/report — 按店铺/SKU/分销商的销售数据（实时）"
        credential={credentials.find((item) => item.source === 'viperp')}
        onConfigure={() => setModalSource('viperp')}
        onRefresh={refresh}
      />
      {modalSource && (
        <PasteCurlModal
          source={modalSource}
          onClose={() => setModalSource(null)}
          onSaved={() => {
            setModalSource(null);
            void refresh();
          }}
        />
      )}
    </div>
  );
}

function CredentialCard({
  source,
  label,
  description,
  credential,
  onConfigure,
  onRefresh,
}: {
  source: KuaimaiSource;
  label: string;
  description: string;
  credential?: Credential;
  onConfigure: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [syncing, setSyncing] = useState(false);
  const [testing, setTesting] = useState(false);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await triggerSync(source);
      if (result.success) {
        toast.success('已在后台开始同步，请到「同步记录」tab 查看进度（约 1-2 分钟）');
      } else {
        toast.error(`触发失败: ${result.error}`);
      }
      await onRefresh();
    } catch (error) {
      toast.error(`触发失败: ${(error as Error).message}`);
    } finally {
      setSyncing(false);
    }
  };

  const handleTest = async () => {
    if (!credential) return;
    setTesting(true);
    try {
      const result = await testCredential(credential.id);
      toast[result.ok ? 'success' : 'error'](result.message);
      await onRefresh();
    } catch (error) {
      toast.error(`测试失败: ${(error as Error).message}`);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!credential || !confirm(`确认删除 ${label} 的凭证？`)) return;
    try {
      await deleteCredential(credential.id);
      toast.success('已删除');
      await onRefresh();
    } catch (error) {
      toast.error(`删除失败: ${(error as Error).message}`);
    }
  };

  if (!credential) {
    return (
      <div className="border border-[var(--s-border-default)] rounded-lg p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold mb-1">{label}</h3>
            <p className="text-sm text-[var(--s-text-secondary)] mb-3">{description}</p>
            <div className="flex items-center gap-1.5 text-sm text-[var(--s-text-secondary)]">
              <XCircle className="w-4 h-4" />
              <span>未配置</span>
            </div>
          </div>
          <button type="button" onClick={onConfigure} className="px-3 py-1.5 text-sm bg-[var(--s-accent)] text-white rounded hover:opacity-90 shrink-0">
            ➕ 配置
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="border border-[var(--s-border-default)] rounded-lg p-5">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <h3 className="text-base font-semibold mb-1">{label}</h3>
          <p className="text-sm text-[var(--s-text-secondary)]">{description}</p>
        </div>
        <button type="button" onClick={handleDelete} className="text-sm text-red-600 hover:underline shrink-0" title="删除凭证">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
      <div className="text-sm space-y-1 mb-4 bg-[var(--s-bg-secondary)] rounded p-3">
        <div className="flex items-center gap-1.5">
          {credential.status === 'active'
            ? <CheckCircle2 className="w-4 h-4 text-green-600" />
            : <AlertTriangle className="w-4 h-4 text-amber-600" />}
          <span className="font-medium">
            {credential.status === 'active' ? '已配置' :
              credential.status === 'expired' ? 'Cookie 已失效，请重新配置' : '凭证无效'}
          </span>
        </div>
        <div className="text-[var(--s-text-secondary)]">
          <div>Companyid: <code>{credential.kuaimai_company_id}</code></div>
          <div>Cookie: <code>{credential.censeid_preview}</code></div>
          {credential.last_sync_at && <div>最近同步: {formatRelativeTime(credential.last_sync_at)}{credential.last_sync_status === 'success' ? ' ✓' : ' ⚠️'}</div>}
          {credential.last_sync_error && <div className="text-red-600 mt-1">{credential.last_sync_error}</div>}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onConfigure} className="px-3 py-1.5 text-sm border border-[var(--s-border-default)] rounded hover:bg-[var(--s-bg-secondary)]">
          ✏️ 更新 Cookie
        </button>
        <button type="button" onClick={handleTest} disabled={testing} className="px-3 py-1.5 text-sm border border-[var(--s-border-default)] rounded hover:bg-[var(--s-bg-secondary)] disabled:opacity-50 flex items-center gap-1">
          {testing && <Loader2 className="w-3 h-3 animate-spin" />} 🔌 测试连接
        </button>
        <button type="button" onClick={handleSync} disabled={syncing || credential.status !== 'active'} className="px-3 py-1.5 text-sm bg-[var(--s-accent)] text-white rounded hover:opacity-90 disabled:opacity-50 flex items-center gap-1">
          {syncing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />} 立即同步
        </button>
      </div>
    </div>
  );
}

function PasteCurlModal({
  source,
  onClose,
  onSaved,
}: {
  source: KuaimaiSource;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!text.trim()) {
      toast.error('请粘贴 cURL');
      return;
    }
    setSaving(true);
    try {
      const result = await createCredential(text, source);
      toast.success(`已保存 — companyid=${result.detected_companyid}`);
      onSaved();
    } catch (error) {
      toast.error(`保存失败: ${(error as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const sourceLabel = source === 'thinktank' ? '智库' : '销售主题报表';
  return (
    <Modal isOpen={true} onClose={onClose} title={`配置 ${sourceLabel} 凭证`} maxWidth="max-w-2xl">
      <div className="space-y-4">
        <div className="text-sm text-[var(--s-text-secondary)] bg-[var(--s-bg-secondary)] rounded p-3">
          <strong>📋 操作步骤：</strong>
          <ol className="list-decimal ml-5 mt-1 space-y-0.5">
            <li>登录 <code>erp.superboss.cc</code></li>
            <li>F12 → Network → 打开当前数据源的报表并执行一次查询</li>
            <li>选择包含 <code>companyid</code> 和 <code>_censeid</code> 的请求</li>
            <li>右键请求 → Copy → Copy as cURL，粘贴到下方</li>
          </ol>
        </div>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="curl 'https://erp.superboss.cc/...' \&#10;  -H 'companyid: 65109' \&#10;  -b '...; _censeid=...; ...'"
          className="w-full h-64 p-3 text-xs font-mono border border-[var(--s-border-default)] rounded resize-y"
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={saving} className="px-4 py-2 text-sm border border-[var(--s-border-default)] rounded hover:bg-[var(--s-bg-secondary)] disabled:opacity-50">
            取消
          </button>
          <button type="button" onClick={handleSave} disabled={saving} className="px-4 py-2 text-sm bg-[var(--s-accent)] text-white rounded hover:opacity-90 disabled:opacity-50 flex items-center gap-2">
            {saving && <Loader2 className="w-4 h-4 animate-spin" />} 解析并保存
          </button>
        </div>
      </div>
    </Modal>
  );
}

function formatRelativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return '刚刚';
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)} 分钟前`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)} 小时前`;
  return `${Math.floor(ms / 86_400_000)} 天前`;
}
