import { useCallback, useEffect, useState } from 'react';
import {
  getOrgMcpConnectorStatus,
  setOrgMcpConnectorEnabled,
  setupOrgMcpConnector,
  type OrgMcpConnectorState,
} from '../../services/org';

interface Props {
  orgId: string;
}

export default function McpConnectorSection({ orgId }: Props) {
  const [connector, setConnector] = useState<OrgMcpConnectorState | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [changingEnabled, setChangingEnabled] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const status = await getOrgMcpConnectorStatus(orgId);
      setConnector(status.data);
    } catch {
      setError('无法读取 MCP 状态，请检查管理员权限或稍后重试。');
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const connect = async () => {
    setConnecting(true);
    setError('');
    setNotice('');
    try {
      const result = await setupOrgMcpConnector(orgId);
      setConnector(result.data);
      if (result.success && result.data.enabled && result.data.health_status === 'ready') {
        setNotice('已连接并启用。现在可以在聊天中让 AI 查询测试 sample。');
      } else {
        setError(`连接失败${result.data.last_error_code ? `（${result.data.last_error_code}）` : ''}，请稍后重试。`);
      }
    } catch {
      setError('连接失败，请确认平台测试服务已开放并稍后重试。');
    } finally {
      setConnecting(false);
    }
  };

  const changeEnabled = async () => {
    if (!connector) return;
    setChangingEnabled(true);
    setError('');
    setNotice('');
    try {
      const result = await setOrgMcpConnectorEnabled(orgId, !connector.enabled);
      setConnector(result.data);
      setNotice(result.data.enabled ? '测试 Connector 已启用。' : '测试 Connector 已停用。');
    } catch {
      setError(connector.enabled ? '停用失败，请稍后重试。' : '启用失败，请先完成连接测试。');
    } finally {
      setChangingEnabled(false);
    }
  };

  if (loading) return <div className="py-8 text-center text-text-tertiary">加载 MCP 状态…</div>;

  const healthLabel = connector?.health_status === 'ready' ? '连接正常'
    : connector?.health_status === 'error' ? '连接异常'
      : connector?.health_status === 'configured' ? '等待连接测试' : '尚未连接';

  return (
    <section className="space-y-4" aria-label="MCP Connector 管理">
      <div className="rounded-lg border border-border-default bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">平台只读测试 MCP</h3>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-text-tertiary">
              一键连接平台审核的测试工具。不需要找 MCP 链接或填写 Token，只读取合成 sample，不连接 CRM、ERP 或真实业务数据。
            </p>
          </div>
          <button type="button" onClick={() => void refresh()} disabled={loading}
            className="rounded-lg border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:bg-hover disabled:opacity-50">
            刷新状态
          </button>
        </div>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-text-tertiary">连接状态</dt><dd className="mt-1 font-medium">{healthLabel}</dd></div>
          <div><dt className="text-xs text-text-tertiary">组织状态</dt><dd className="mt-1 font-medium">{connector?.enabled ? '已启用' : '未启用'}</dd></div>
          <div><dt className="text-xs text-text-tertiary">逻辑能力</dt><dd className="mt-1 font-medium"><code>test.sample.read</code></dd></div>
          <div><dt className="text-xs text-text-tertiary">工具</dt><dd className="mt-1 font-medium"><code>mcp_test_lookup</code></dd></div>
        </dl>
        {connector?.last_checked_at && <p className="mt-3 text-xs text-text-tertiary">最近检查：{new Date(connector.last_checked_at).toLocaleString()}</p>}
        {connector?.last_error_code && <p className="mt-1 text-xs text-error">最近错误：{connector.last_error_code}</p>}
      </div>

      <div className="rounded-lg border border-border-default bg-surface p-4">
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void connect()} disabled={connecting || changingEnabled}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-text-on-accent hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50">
            {connecting ? '连接测试中…' : connector?.enabled ? '重新测试连接' : '一键连接并启用'}
          </button>
          {connector?.enabled && <button type="button" onClick={() => void changeEnabled()} disabled={changingEnabled || connecting}
            className="rounded-lg border border-border-default px-4 py-2 text-sm text-text-primary hover:bg-hover disabled:opacity-50">
            {changingEnabled ? '处理中…' : '停用'}
          </button>}
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-error">{error}</p>}
        {notice && <p role="status" className="mt-3 text-sm text-success">{notice}</p>}
        <p className="mt-3 text-xs leading-5 text-text-tertiary">
          连接通过后，在新聊天中直接说“用 MCP 查询 sample 记录”。每次调用仍经过现有权限检查和审计。
        </p>
      </div>
    </section>
  );
}
