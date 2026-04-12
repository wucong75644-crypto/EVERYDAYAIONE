/**
 * 系统错误监控面板
 *
 * 功能：错误列表 + 统计摘要 + AI 分析 + 标记处理 + 批量清除
 * 权限：仅 super_admin 可见
 */

import { useState, useEffect, useCallback } from 'react';
import {
  listErrors,
  getErrorStats,
  summarizeErrors,
  resolveError,
  clearErrors,
  type ErrorLogItem,
  type ErrorStatsResponse,
  type ErrorListParams,
} from '../../services/errorMonitor';

export default function ErrorMonitorPanel() {
  // ── 状态 ───────────────────────────────────────────────
  const [items, setItems] = useState<ErrorLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<ErrorStatsResponse | null>(null);
  const [summary, setSummary] = useState('');
  const [summarizing, setSummarizing] = useState(false);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // 筛选
  const [filterLevel, setFilterLevel] = useState<string>('');
  const [filterResolved, setFilterResolved] = useState<string>('false'); // 默认未处理
  const [filterDays, setFilterDays] = useState(7);
  const [search, setSearch] = useState('');

  const pageSize = 20;

  // ── 数据加载 ──────────────────────────────────────────

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params: ErrorListParams = {
        page,
        page_size: pageSize,
        days: filterDays,
      };
      if (filterLevel) params.level = filterLevel;
      if (filterResolved !== '') params.is_resolved = filterResolved === 'true';
      if (search.trim()) params.search = search.trim();

      const [listResult, statsResult] = await Promise.all([
        listErrors(params),
        page === 1 ? getErrorStats() : Promise.resolve(null),
      ]);

      setItems(listResult.items);
      setTotal(listResult.total);
      if (statsResult) setStats(statsResult);
    } catch {
      setError('加载错误日志失败');
    } finally {
      setLoading(false);
    }
  }, [page, filterLevel, filterResolved, filterDays, search]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── 操作 ──────────────────────────────────────────────

  const handleResolve = async (id: number) => {
    try {
      await resolveError(id);
      setItems((prev) => prev.map((item) =>
        item.id === id ? { ...item, is_resolved: true, resolved_at: new Date().toISOString() } : item,
      ));
      if (stats) setStats({ ...stats, unresolved: Math.max(0, stats.unresolved - 1) });
    } catch {
      setError('标记处理失败');
    }
  };

  const handleSummarize = async () => {
    setSummarizing(true);
    setSummary('');
    try {
      const result = await summarizeErrors(filterDays);
      setSummary(result.summary);
    } catch {
      setError('AI 分析失败');
    } finally {
      setSummarizing(false);
    }
  };

  const handleClear = async () => {
    if (!confirm('确定清除已处理的错误日志？')) return;
    try {
      const result = await clearErrors(true);
      setError('');
      alert(`已清除 ${result.deleted} 条记录`);
      loadData();
    } catch {
      setError('清除失败');
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  // ── 渲染 ──────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* 统计卡片 */}
      {stats && (
        <div className="grid grid-cols-4 gap-3">
          <StatCard label="今日错误" value={stats.today_total} />
          <StatCard label="今日致命" value={stats.today_critical} danger={stats.today_critical > 0} />
          <StatCard label="本周总计" value={stats.week_total} />
          <StatCard label="未处理" value={stats.unresolved} warn={stats.unresolved > 10} />
        </div>
      )}

      {/* 筛选栏 */}
      <div className="flex flex-wrap gap-2 items-center">
        <select
          value={filterLevel}
          onChange={(e) => { setFilterLevel(e.target.value); setPage(1); }}
          className="px-2 py-1.5 rounded-lg border border-border bg-surface-card text-text-secondary text-sm"
        >
          <option value="">所有级别</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>

        <select
          value={filterResolved}
          onChange={(e) => { setFilterResolved(e.target.value); setPage(1); }}
          className="px-2 py-1.5 rounded-lg border border-border bg-surface-card text-text-secondary text-sm"
        >
          <option value="false">未处理</option>
          <option value="true">已处理</option>
          <option value="">全部</option>
        </select>

        <select
          value={filterDays}
          onChange={(e) => { setFilterDays(Number(e.target.value)); setPage(1); }}
          className="px-2 py-1.5 rounded-lg border border-border bg-surface-card text-text-secondary text-sm"
        >
          <option value={1}>今天</option>
          <option value={3}>近3天</option>
          <option value={7}>近7天</option>
          <option value={30}>近30天</option>
        </select>

        <input
          type="text"
          placeholder="搜索错误消息..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { setPage(1); loadData(); } }}
          className="px-3 py-1.5 rounded-lg border border-border bg-surface-card text-text-secondary text-sm flex-1 min-w-[150px]"
        />

        <button
          onClick={handleSummarize}
          disabled={summarizing}
          className="px-3 py-1.5 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 disabled:opacity-50 transition-base"
        >
          {summarizing ? 'AI 分析中...' : 'AI 分析'}
        </button>

        <button
          onClick={handleClear}
          className="px-3 py-1.5 rounded-lg bg-surface-hover text-text-tertiary text-sm hover:text-danger transition-base"
        >
          清除已处理
        </button>
      </div>

      {/* AI 分析结果 */}
      {summary && (
        <div className="p-4 rounded-lg bg-accent/5 border border-accent/20">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium text-accent">AI 分析报告</h4>
            <button
              onClick={() => setSummary('')}
              className="text-text-disabled hover:text-text-tertiary text-xs"
            >
              关闭
            </button>
          </div>
          <div className="text-sm text-text-secondary whitespace-pre-wrap">{summary}</div>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="p-3 rounded-lg bg-danger/10 text-danger text-sm">{error}</div>
      )}

      {/* 错误列表 */}
      {loading ? (
        <div className="text-center py-8 text-text-tertiary">加载中...</div>
      ) : items.length === 0 ? (
        <div className="text-center py-8 text-text-tertiary">没有错误记录</div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <ErrorRow
              key={item.id}
              item={item}
              expanded={expandedId === item.id}
              onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
              onResolve={() => handleResolve(item.id)}
            />
          ))}
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-text-tertiary">
          <span>共 {total} 条，第 {page}/{totalPages} 页</span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="px-3 py-1 rounded border border-border hover:bg-hover disabled:opacity-30 transition-base"
            >
              上一页
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1 rounded border border-border hover:bg-hover disabled:opacity-30 transition-base"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


// ── 子组件 ──────────────────────────────────────────────

function StatCard({ label, value, danger, warn }: {
  label: string;
  value: number;
  danger?: boolean;
  warn?: boolean;
}) {
  const colorClass = danger ? 'text-danger' : warn ? 'text-warning' : 'text-text-primary';
  return (
    <div className="p-3 rounded-lg bg-surface-hover text-center">
      <div className={`text-xl font-bold ${colorClass}`}>{value}</div>
      <div className="text-xs text-text-tertiary mt-1">{label}</div>
    </div>
  );
}

function ErrorRow({ item, expanded, onToggle, onResolve }: {
  item: ErrorLogItem;
  expanded: boolean;
  onToggle: () => void;
  onResolve: () => void;
}) {
  const levelColor = item.level === 'CRITICAL' ? 'bg-danger text-white' : 'bg-warning/20 text-warning';
  const time = new Date(item.last_seen_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

  return (
    <div className={`rounded-lg border ${item.is_critical ? 'border-danger/30' : 'border-border'} ${item.is_resolved ? 'opacity-60' : ''}`}>
      {/* 主行 */}
      <div
        className="flex items-center gap-3 p-3 cursor-pointer hover:bg-hover/50 transition-base"
        onClick={onToggle}
      >
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${levelColor}`}>
          {item.level}
        </span>

        <span className="text-xs text-text-disabled truncate max-w-[120px]">
          {item.module}
        </span>

        <span className="text-sm text-text-secondary flex-1 truncate">
          {item.message}
        </span>

        <span className="text-xs text-text-disabled whitespace-nowrap">
          x{item.occurrence_count}
        </span>

        <span className="text-xs text-text-disabled whitespace-nowrap">
          {time}
        </span>

        {!item.is_resolved && (
          <button
            onClick={(e) => { e.stopPropagation(); onResolve(); }}
            className="px-2 py-0.5 rounded text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-base"
          >
            处理
          </button>
        )}
        {item.is_resolved && (
          <span className="text-xs text-success">已处理</span>
        )}
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-border/50 space-y-2">
          <div className="grid grid-cols-2 gap-2 text-xs text-text-tertiary mt-2">
            <div>指纹: {item.fingerprint}</div>
            <div>函数: {item.function}:{item.line}</div>
            <div>首次: {new Date(item.first_seen_at).toLocaleString('zh-CN')}</div>
            <div>末次: {time}</div>
            {item.org_id && <div className="col-span-2">企业: {item.org_id}</div>}
          </div>
          {item.traceback && (
            <pre className="text-xs text-text-disabled bg-surface-hover p-2 rounded overflow-x-auto max-h-40 whitespace-pre-wrap">
              {item.traceback}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
