/**
 * 快麦 Web 数据接入 — 管理员配置面板
 *
 * 包含 3 个 tab：
 *   1. 数据源：智库 + viperp 凭证状态卡片，粘贴 cURL 配置
 *   2. 同步记录：最近同步历史
 *   3. 运营管理：列出所有运营，手动绑定企微
 */

import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import {
  Database,
  Clock,
  Users,
  Loader2,
} from 'lucide-react';
import {
  bindOperator,
  listOperators,
  listSyncLogs,
  unbindOperator,
  type Operator,
  type SyncLog,
} from '../../services/kuaimaiExternal';
import { cn } from '../../utils/cn';
import Modal from '../common/Modal';
import KuaimaiSourcesTab from './KuaimaiSourcesTab';


type TabKey = 'sources' | 'logs' | 'operators';

export default function KuaimaiIntegrationPanel() {
  const [tab, setTab] = useState<TabKey>('sources');

  return (
    <div className="flex flex-col h-full">
      {/* Tab 导航 */}
      <div className="flex border-b border-[var(--s-border-default)] mb-4">
        <TabButton
          active={tab === 'sources'}
          onClick={() => setTab('sources')}
          icon={<Database className="w-4 h-4" />}
          label="数据源"
        />
        <TabButton
          active={tab === 'logs'}
          onClick={() => setTab('logs')}
          icon={<Clock className="w-4 h-4" />}
          label="同步记录"
        />
        <TabButton
          active={tab === 'operators'}
          onClick={() => setTab('operators')}
          icon={<Users className="w-4 h-4" />}
          label="运营管理"
        />
      </div>

      {/* Tab 内容 */}
      <div className="flex-1 overflow-auto">
        {tab === 'sources' && <KuaimaiSourcesTab />}
        {tab === 'logs' && <LogsTab />}
        {tab === 'operators' && <OperatorsTab />}
      </div>
    </div>
  );
}


// ──────────────────────── Tab 2: 同步记录 ────────────────────────

function LogsTab() {
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const fetchOnce = async () => {
      try {
        const data = await listSyncLogs(undefined, 50);
        if (cancelled) return;
        setLogs(data);
        setLoading(false);
        // 如果有 running，5 秒后自动刷新（看进度）
        const hasRunning = data.some((l) => l.status === 'running');
        if (hasRunning) {
          timer = window.setTimeout(fetchOnce, 5000);
        }
      } catch (e) {
        if (!cancelled) {
          toast.error(`加载失败: ${(e as Error).message}`);
          setLoading(false);
        }
      }
    };

    fetchOnce();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  if (loading) return <div className="text-center py-8">加载中...</div>;
  if (logs.length === 0) {
    return (
      <div className="text-center py-12 text-[var(--s-text-secondary)]">
        暂无同步记录
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-[var(--s-text-secondary)] text-xs uppercase border-b border-[var(--s-border-default)]">
          <tr>
            <th className="py-2">时间</th>
            <th>数据源</th>
            <th>类型</th>
            <th>状态</th>
            <th>行数</th>
            <th>时间范围</th>
            <th>耗时</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.id} className="border-b border-[var(--s-border-default)]">
              <td className="py-2">{formatTime(log.started_at)}</td>
              <td>{log.source}</td>
              <td>{log.sync_type}</td>
              <td>
                <StatusBadge status={log.status} />
                {log.error_message && (
                  <div className="text-xs text-red-600 mt-0.5 max-w-xs truncate" title={log.error_message}>
                    {log.error_message}
                  </div>
                )}
              </td>
              <td>{log.rows_synced}</td>
              <td className="text-xs text-[var(--s-text-secondary)]">
                {log.date_range_start} ~ {log.date_range_end}
              </td>
              <td className="text-xs">{formatDuration(log.started_at, log.finished_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles =
    status === 'success'
      ? 'text-green-700 bg-green-50'
      : status === 'failed'
      ? 'text-red-700 bg-red-50'
      : 'text-amber-700 bg-amber-50';
  return <span className={cn('px-2 py-0.5 rounded text-xs', styles)}>{status}</span>;
}


// ──────────────────────── Tab 3: 运营管理 ────────────────────────

function OperatorsTab() {
  const [operators, setOperators] = useState<Operator[]>([]);
  const [loading, setLoading] = useState(true);
  const [onlyUnbound, setOnlyUnbound] = useState(false);
  const [bindingOp, setBindingOp] = useState<Operator | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await listOperators(onlyUnbound);
      setOperators(data);
    } catch (e) {
      toast.error(`加载失败: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, [onlyUnbound]);

  const handleUnbind = async (op: Operator) => {
    if (!confirm(`确认解绑 ${op.operator_name}?`)) return;
    try {
      await unbindOperator(op.id);
      toast.success('已解绑');
      refresh();
    } catch (e) {
      toast.error(`失败: ${(e as Error).message}`);
    }
  };

  if (loading) return <div className="text-center py-8">加载中...</div>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={onlyUnbound}
            onChange={(e) => setOnlyUnbound(e.target.checked)}
          />
          只看未绑定
        </label>
        <button
          type="button"
          onClick={refresh}
          className="text-sm text-[var(--s-accent)] hover:underline ml-auto"
        >
          刷新
        </button>
      </div>

      {operators.length === 0 ? (
        <div className="text-center py-12 text-[var(--s-text-secondary)]">
          {onlyUnbound ? '所有运营都已绑定 ✓' : '暂无运营数据（请先同步 viperp）'}
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-[var(--s-text-secondary)] text-xs uppercase border-b border-[var(--s-border-default)]">
            <tr>
              <th className="py-2">运营名</th>
              <th>店铺数</th>
              <th>状态</th>
              <th>企微账号</th>
              <th>备注</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {operators.map((op) => (
              <tr key={op.id} className="border-b border-[var(--s-border-default)]">
                <td className="py-2 font-medium">{op.operator_name}</td>
                <td>{op.shop_count}</td>
                <td>
                  {op.is_bound ? (
                    <span className="text-green-700">✓ 已绑定</span>
                  ) : (
                    <span className="text-amber-700">⚠️ 未绑定</span>
                  )}
                </td>
                <td className="text-xs">
                  {op.wecom_userid ? <code>{op.wecom_userid}</code> : '-'}
                </td>
                <td className="text-xs text-[var(--s-text-secondary)] max-w-xs truncate" title={op.notes || ''}>
                  {op.notes || '-'}
                </td>
                <td>
                  {op.is_bound ? (
                    <button
                      type="button"
                      onClick={() => handleUnbind(op)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      解绑
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setBindingOp(op)}
                      className="text-xs text-[var(--s-accent)] hover:underline"
                    >
                      手动绑定
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {bindingOp && (
        <BindOperatorModal
          operator={bindingOp}
          onClose={() => setBindingOp(null)}
          onSaved={() => {
            setBindingOp(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function BindOperatorModal({
  operator,
  onClose,
  onSaved,
}: {
  operator: Operator;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [wecomUserid, setWecomUserid] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!wecomUserid.trim()) {
      toast.error('请填写企微 UserId');
      return;
    }
    setSaving(true);
    try {
      await bindOperator(operator.id, wecomUserid.trim());
      toast.success('绑定成功');
      onSaved();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
                || (e as Error).message;
      toast.error(`绑定失败: ${msg}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal isOpen={true} onClose={onClose} title={`绑定运营: ${operator.operator_name}`}>
      <div className="space-y-4">
        <div className="text-sm text-[var(--s-text-secondary)]">
          请输入这位运营对应的 <strong>企微 UserId</strong>。
          可以在企微管理后台 → 通讯录 → 成员详情查看。
        </div>
        <input
          type="text"
          value={wecomUserid}
          onChange={(e) => setWecomUserid(e.target.value)}
          placeholder="例如：WuCong"
          className="w-full px-3 py-2 border border-[var(--s-border-default)] rounded text-sm"
          autoFocus
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm border border-[var(--s-border-default)] rounded hover:bg-[var(--s-bg-secondary)]"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm bg-[var(--s-accent)] text-white rounded hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            保存
          </button>
        </div>
      </div>
    </Modal>
  );
}


// ──────────────────────── 工具 ────────────────────────

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
        active
          ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
          : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]',
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatDuration(start: string, end: string | null): string {
  if (!end) return '进行中';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}
