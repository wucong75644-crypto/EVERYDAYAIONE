import { useCallback, useEffect, useState } from 'react';
import {
  getOrgMcpConnectorStatus,
  getOrgMcpCredentialStatus,
  revokeOrgMcpCredential,
  setOrgMcpConnectorEnabled,
  setOrgMcpCredential,
  testOrgMcpConnector,
  type OrgMcpConnectorState,
  type OrgMcpCredentialStatus,
} from '../../services/org';

interface Props {
  orgId: string;
}

export default function McpConnectorSection({ orgId }: Props) {
  const [connector, setConnector] = useState<OrgMcpConnectorState | null>(null);
  const [credential, setCredential] = useState<OrgMcpCredentialStatus | null>(null);
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(true);
  const [savingCredential, setSavingCredential] = useState(false);
  const [testing, setTesting] = useState(false);
  const [changingEnabled, setChangingEnabled] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [status, credentialStatus] = await Promise.all([
        getOrgMcpConnectorStatus(orgId),
        getOrgMcpCredentialStatus(orgId),
      ]);
      setConnector(status.data);
      setCredential(credentialStatus.data);
    } catch {
      setError('无法读取 Connector 状态，请检查管理员权限或稍后重试。');
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const saveCredential = async () => {
    const value = token.trim();
    if (!value) {
      setError('请输入合成测试凭证。');
      return;
    }
    setSavingCredential(true);
    setError('');
    setNotice('');
    try {
      const result = await setOrgMcpCredential(orgId, value);
      setCredential(result.data);
      setToken('');
      setNotice('测试凭证已加密保存，页面不会回显。请先测试连接，再启用 Connector。');
    } catch {
      setError('凭证保存失败，请确认管理员权限和平台凭证存储状态。');
    } finally {
      setSavingCredential(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setError('');
    setNotice('');
    try {
      const result = await testOrgMcpConnector(orgId);
      setConnector(result.data);
      if (result.success && result.data.health_status === 'ready') {
        setNotice('连接测试通过：服务健康，工具清单与平台审核配置一致。');
      } else {
        setError(`连接测试未通过${result.data.last_error_code ? `（${result.data.last_error_code}）` : ''}。`);
      }
    } catch {
      setError('连接测试请求失败，请检查管理员权限和平台服务状态。');
    } finally {
      setTesting(false);
    }
  };

  const changeEnabled = async () => {
    if (!connector) return;
    const nextEnabled = !connector.enabled;
    if (nextEnabled && !credential?.configured) {
      setError('请先保存合成测试凭证。');
      return;
    }
    setChangingEnabled(true);
    setError('');
    setNotice('');
    try {
      const result = await setOrgMcpConnectorEnabled(orgId, nextEnabled);
      setConnector(result.data);
      setNotice(nextEnabled ? 'Connector 已为本组织启用。' : 'Connector 已停用。');
    } catch {
      setError(nextEnabled
        ? '启用失败，请先配置凭证并确认平台已开放此 Connector。'
        : '停用失败，请稍后重试。');
    } finally {
      setChangingEnabled(false);
    }
  };

  const revokeCredential = async () => {
    if (!credential?.configured || connector?.enabled) return;
    if (!window.confirm('撤销本组织的 MCP 测试凭证？撤销后需要重新录入才能连接。')) return;
    setRevoking(true);
    setError('');
    setNotice('');
    try {
      const result = await revokeOrgMcpCredential(orgId, credential.version);
      setCredential(result.data);
      setNotice('测试凭证已撤销。');
    } catch {
      setError('凭证撤销失败，请刷新状态后重试。');
    } finally {
      setRevoking(false);
    }
  };

  if (loading) return <div className="py-8 text-center text-text-tertiary">加载 MCP Connector 状态…</div>;

  const healthLabel = connector?.health_status === 'ready' ? '连接正常'
    : connector?.health_status === 'error' ? '连接异常'
      : connector?.health_status === 'configured' ? '等待连接测试' : '尚未测试';

  return (
    <section className="space-y-4" aria-label="MCP Connector 管理">
      <div className="rounded-lg border border-border-default bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">平台审核的只读测试 Connector</h3>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-text-tertiary">
              当前只提供合成 sample 数据，不连接 CRM、ERP 或其他业务系统。组织管理员可保存测试凭证、检查连接并控制本组织是否启用。
            </p>
          </div>
          <button type="button" onClick={() => void refresh()} disabled={loading}
            className="rounded-lg border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:bg-hover disabled:opacity-50">
            刷新状态
          </button>
        </div>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-text-tertiary">Connector</dt><dd className="mt-1 font-medium">test-readonly</dd></div>
          <div><dt className="text-xs text-text-tertiary">逻辑能力</dt><dd className="mt-1 font-medium"><code>test.sample.read</code></dd></div>
          <div><dt className="text-xs text-text-tertiary">可调用工具</dt><dd className="mt-1 font-medium"><code>mcp_test_lookup</code></dd></div>
          <div><dt className="text-xs text-text-tertiary">连接状态</dt><dd className="mt-1 font-medium">{healthLabel}</dd></div>
          <div><dt className="text-xs text-text-tertiary">组织状态</dt><dd className="mt-1 font-medium">{connector?.enabled ? '已启用' : '已停用'}</dd></div>
          <div><dt className="text-xs text-text-tertiary">测试凭证</dt><dd className="mt-1 font-medium">{credential?.configured ? '已配置（内容不可查看）' : '未配置'}</dd></div>
        </dl>
        {connector?.last_checked_at && <p className="mt-3 text-xs text-text-tertiary">最近检查：{new Date(connector.last_checked_at).toLocaleString()}</p>}
        {connector?.last_error_code && <p className="mt-1 text-xs text-error">最近错误：{connector.last_error_code}</p>}
      </div>

      <div className="rounded-lg border border-border-default bg-surface p-4">
        <label htmlFor="mcp-test-credential" className="block text-sm font-medium text-text-primary">合成测试凭证</label>
        <p className="mt-1 text-xs leading-5 text-text-tertiary">
          仅输入平台提供的测试凭证。凭证会按组织加密保存，不进入 Skill 或模型上下文，保存后不会回显；不要粘贴生产业务凭证。
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input id="mcp-test-credential" type="password" autoComplete="new-password" maxLength={4096}
            value={token} onChange={(event) => setToken(event.target.value)}
            placeholder={credential?.configured ? '输入新测试凭证以替换' : '输入合成测试凭证'}
            className="min-w-0 flex-1 rounded-lg border border-border-default bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring" />
          <button type="button" onClick={() => void saveCredential()} disabled={savingCredential || !token.trim()}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-text-on-accent hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50">
            {savingCredential ? '保存中…' : '安全保存'}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => void testConnection()} disabled={testing || !credential?.configured}
            className="rounded-lg border border-border-default px-4 py-2 text-sm text-text-primary hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50">
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button type="button" onClick={() => void changeEnabled()} disabled={changingEnabled || (!connector?.enabled && !credential?.configured)}
            className="rounded-lg border border-border-default px-4 py-2 text-sm text-text-primary hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50">
            {changingEnabled ? '处理中…' : connector?.enabled ? '停用 Connector' : '启用 Connector'}
          </button>
          {credential?.configured && !connector?.enabled && <button type="button" onClick={() => void revokeCredential()} disabled={revoking}
            className="rounded-lg px-3 py-2 text-sm text-error hover:bg-error-light disabled:opacity-50">
            {revoking ? '撤销中…' : '撤销凭证'}
          </button>}
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-error">{error}</p>}
        {notice && <p role="status" className="mt-3 text-sm text-success">{notice}</p>}
        <p className="mt-3 text-xs leading-5 text-text-tertiary">
          连接测试会检查服务健康状态和审核过的工具清单；启用后，在聊天中请求读取 sample 才会执行实际工具调用并写入审计。
        </p>
      </div>
    </section>
  );
}
