/**
 * 系统错误监控面板
 *
 * 功能：错误列表 + 统计摘要 + AI 分析 + 标记处理 + 批量清除
 * 权限：仅 super_admin 可见
 *
 * V2 重构：对齐设计系统（Button/Card/Modal/framer-motion）
 */

import { useState, useEffect, useCallback } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import { AlertTriangle, Loader2, Sparkles, Trash2, ChevronDown, CheckCircle2, X } from 'lucide-react';
import {
  listErrors,
  getErrorStats,
  getImagePlatformCosts,
  type ImagePlatformCostReport,
  summarizeErrors,
  resolveError,
  clearErrors,
  type ErrorLogItem,
  type ErrorStatsResponse,
  type ErrorListParams,
} from '../../services/errorMonitor';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import Modal from '../common/Modal';
import { SOFT_SPRING, staggerContainer, staggerItem, slideUpVariants } from '../../utils/motion';
import { cn } from '../../utils/cn';

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
  const [filterResolved, setFilterResolved] = useState<string>('false');
  const [filterDays, setFilterDays] = useState(7);
  const [search, setSearch] = useState('');
  const [costRefresh, setCostRefresh] = useState(0);
  const [imageCosts, setImageCosts] = useState<ImagePlatformCostReport | null>(null);
  const [costError, setCostError] = useState('');
  const [costPage, setCostPage] = useState(1);

  // 清除确认弹窗
  const [clearModalOpen, setClearModalOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearResult, setClearResult] = useState<number | null>(null);

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

  useEffect(() => { setCostPage(1); }, [filterDays]);
  useEffect(() => {
    let active = true;
    setCostError('');
    getImagePlatformCosts(filterDays, costPage).then((result) => {
      if (active) setImageCosts(result);
    }).catch(() => {
      if (active) { setImageCosts(null); setCostError('平台承担统计暂时不可用'); }
    });
    return () => { active = false; };
  }, [filterDays, costPage, costRefresh]);

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
    setClearing(true);
    try {
      const result = await clearErrors(true);
      setError('');
      setClearResult(result.deleted);
      setClearModalOpen(false);
      loadData();
    } catch {
      setError('清除失败');
    } finally {
      setClearing(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  const selectClass = 'px-2 py-1.5 rounded-[var(--s-radius-control)] border border-[var(--s-border-default)] bg-[var(--c-card-bg)] text-[var(--s-text-secondary)] text-sm';

  // ── 渲染 ──────────────────────────────────────────────
  return (
    <div className="space-y-4">
      <Card className="p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="font-medium">图片生成 · 平台承担（近 {filterDays} 天）</div>
          <Button size="sm" variant="secondary" onClick={() => setCostRefresh(value => value + 1)}>刷新承担统计</Button>
        </div>
        <p className="text-sm text-[var(--s-text-secondary)]">
          受理结果无法确认或输出合同不合格的任务已退还用户积分。供应商成本按接受时价格估算，实际支出待账单核实。
        </p>
        {costError && <p className="text-sm text-[var(--s-text-secondary)]">{costError}</p>}
        {imageCosts && <>
          <div className="grid grid-cols-3 gap-3">
            <StatCard label="承担任务数" value={imageCosts.summary.count} />
            <StatCard label="已退用户积分" value={imageCosts.summary.refunded_user_credits} />
            <StatCard label="估算供应商积分" value={imageCosts.summary.estimated_provider_credits} />
          </div>
          {imageCosts.items.length > 0 && <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead><tr><th className="py-2">任务 / 时间</th><th>模型 / 分辨率</th><th>承担原因</th><th>退款</th><th>估算成本</th></tr></thead>
              <tbody>{imageCosts.items.map((item) => <tr key={item.task_id} className="border-t border-[var(--s-border-default)]">
                <td className="py-2"><div className="break-all">{item.task_id}</div><div>{new Date(item.completed_at).toLocaleString()}</div></td>
                <td>{item.cost.model} / {item.cost.resolution || '默认'}</td>
                <td>{item.cost.reason === 'image_output_contract_failure' ? '供应商结果不合格' : '受理未确认'}</td>
                <td>{item.cost.refunded_user_credits}</td><td>{item.cost.estimated_provider_credits}</td>
              </tr>)}</tbody>
            </table>
            <div className="flex items-center gap-3 mt-3">
              <Button variant="secondary" disabled={costPage === 1} onClick={() => setCostPage(costPage - 1)}>上一页</Button>
              <span>第 {costPage} 页</span>
              <Button variant="secondary" disabled={costPage * imageCosts.page_size >= imageCosts.summary.count} onClick={() => setCostPage(costPage + 1)}>下一页</Button>
            </div>
          </div>}
        </>}
      </Card>
      {/* 统计卡片 */}
      {stats && (
        <m.div
          className="grid grid-cols-4 gap-3"
          variants={staggerContainer}
          initial="initial"
          animate="animate"
        >
          <StatCard label="今日错误" value={stats.today_total} />
          <StatCard label="今日致命" value={stats.today_critical} danger={stats.today_critical > 0} />
          <StatCard label="本周总计" value={stats.week_total} />
          <StatCard label="未处理" value={stats.unresolved} warn={stats.unresolved > 10} />
        </m.div>
      )}

      {/* 筛选栏 */}
      <div className="flex flex-wrap gap-2 items-center">
        <select
          value={filterLevel}
          onChange={(e) => { setFilterLevel(e.target.value); setPage(1); }}
          className={selectClass}
        >
          <option value="">所有级别</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>

        <select
          value={filterResolved}
          onChange={(e) => { setFilterResolved(e.target.value); setPage(1); }}
          className={selectClass}
        >
          <option value="false">未处理</option>
          <option value="true">已处理</option>
          <option value="">全部</option>
        </select>

        <select
          value={filterDays}
          onChange={(e) => { setFilterDays(Number(e.target.value)); setPage(1); }}
          className={selectClass}
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
          className="px-3 py-1.5 rounded-[var(--s-radius-control)] border border-[var(--s-border-default)] bg-[var(--c-card-bg)] text-[var(--s-text-secondary)] text-sm flex-1 min-w-[150px] placeholder:text-[var(--s-text-disabled)]"
        />

        <Button
          variant="accent"
          size="sm"
          icon={<Sparkles className="w-3.5 h-3.5" />}
          onClick={handleSummarize}
          loading={summarizing}
        >
          AI 分析
        </Button>

        <Button
          variant="ghost"
          size="sm"
          icon={<Trash2 className="w-3.5 h-3.5" />}
          onClick={() => setClearModalOpen(true)}
        >
          清除已处理
        </Button>
      </div>

      {/* AI 分析结果 */}
      <AnimatePresence>
        {summary && (
          <m.div
            variants={slideUpVariants}
            initial="initial"
            animate="animate"
            exit="exit"
            className="p-4 rounded-[var(--s-radius-card)] bg-[var(--s-accent)]/5 border border-[var(--s-accent)]/20"
          >
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-medium text-[var(--s-accent)]">AI 分析报告</h4>
              <Button variant="ghost" size="sm" onClick={() => setSummary('')}>
                <X className="w-3.5 h-3.5" />
              </Button>
            </div>
            <div className="text-sm text-[var(--s-text-secondary)] whitespace-pre-wrap">{summary}</div>
          </m.div>
        )}
      </AnimatePresence>

      {/* 清除成功提示 */}
      <AnimatePresence>
        {clearResult !== null && (
          <m.div
            variants={slideUpVariants}
            initial="initial"
            animate="animate"
            exit="exit"
            className="p-3 rounded-[var(--s-radius-card)] bg-[var(--s-success)]/10 text-[var(--s-success)] text-sm flex items-center justify-between"
          >
            <span>已清除 {clearResult} 条记录</span>
            <Button variant="ghost" size="sm" onClick={() => setClearResult(null)}>
              <X className="w-3.5 h-3.5" />
            </Button>
          </m.div>
        )}
      </AnimatePresence>

      {/* 错误提示 */}
      <AnimatePresence>
        {error && (
          <m.div
            variants={slideUpVariants}
            initial="initial"
            animate="animate"
            exit="exit"
            className="p-3 rounded-[var(--s-radius-card)] bg-[var(--s-error)]/10 text-[var(--s-error)] text-sm flex items-center justify-between"
          >
            <span>{error}</span>
            <Button variant="ghost" size="sm" onClick={() => setError('')}>
              <X className="w-3.5 h-3.5" />
            </Button>
          </m.div>
        )}
      </AnimatePresence>

      {/* 错误列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12 text-[var(--s-text-tertiary)]">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          <span className="text-sm">加载中...</span>
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-[var(--s-text-tertiary)] text-sm">没有错误记录</div>
      ) : (
        <m.div
          className="space-y-2"
          variants={staggerContainer}
          initial="initial"
          animate="animate"
        >
          {items.map((item) => (
            <ErrorRow
              key={item.id}
              item={item}
              expanded={expandedId === item.id}
              onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
              onResolve={() => handleResolve(item.id)}
            />
          ))}
        </m.div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-[var(--s-text-tertiary)]">
          <span>共 {total} 条，第 {page}/{totalPages} 页</span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
            >
              上一页
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* 清除确认弹窗 */}
      <ClearConfirmModal
        isOpen={clearModalOpen}
        loading={clearing}
        onConfirm={handleClear}
        onCancel={() => setClearModalOpen(false)}
      />
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
  const colorClass = danger
    ? 'text-[var(--s-error)]'
    : warn
      ? 'text-[var(--s-warning)]'
      : 'text-[var(--s-text-primary)]';

  return (
    <Card variant="default" padding="sm">
      <m.div variants={staggerItem} className="text-center">
        <div className={cn('text-xl font-bold', colorClass)}>{value}</div>
        <div className="text-xs text-[var(--s-text-tertiary)] mt-1">{label}</div>
      </m.div>
    </Card>
  );
}

function ErrorRow({ item, expanded, onToggle, onResolve }: {
  item: ErrorLogItem;
  expanded: boolean;
  onToggle: () => void;
  onResolve: () => void;
}) {
  const levelColor = item.level === 'CRITICAL'
    ? 'bg-[var(--s-error)] text-white'
    : 'bg-[var(--s-warning)]/20 text-[var(--s-warning)]';
  const time = new Date(item.last_seen_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

  return (
    <m.div variants={staggerItem}>
      <Card
        variant="default"
        padding="none"
        className={cn(
          item.is_critical && 'border-[var(--s-error)]/30',
          item.is_resolved && 'opacity-60',
        )}
      >
        {/* 主行 */}
        <div
          className="flex items-center gap-3 p-3 cursor-pointer hover:bg-[var(--s-hover)]/50 transition-colors"
          onClick={onToggle}
        >
          <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-bold', levelColor)}>
            {item.level}
          </span>

          <span className="text-xs text-[var(--s-text-disabled)] truncate max-w-[120px]">
            {item.module}
          </span>

          <span className="text-sm text-[var(--s-text-secondary)] flex-1 truncate">
            {item.message}
          </span>

          <span className="text-xs text-[var(--s-text-disabled)] whitespace-nowrap">
            x{item.occurrence_count}
          </span>

          <span className="text-xs text-[var(--s-text-disabled)] whitespace-nowrap">
            {time}
          </span>

          {!item.is_resolved ? (
            <Button
              variant="ghost"
              size="sm"
              icon={<CheckCircle2 className="w-3.5 h-3.5" />}
              onClick={(e) => { e.stopPropagation(); onResolve(); }}
              className="text-[var(--s-accent)]"
            >
              处理
            </Button>
          ) : (
            <span className="text-xs text-[var(--s-success)]">已处理</span>
          )}

          <m.span
            animate={{ rotate: expanded ? 180 : 0 }}
            transition={SOFT_SPRING}
          >
            <ChevronDown className="w-4 h-4 text-[var(--s-text-disabled)]" />
          </m.span>
        </div>

        {/* 展开详情 */}
        <AnimatePresence>
          {expanded && (
            <m.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={SOFT_SPRING}
              className="overflow-hidden"
            >
              <div className="px-3 pb-3 border-t border-[var(--s-border-subtle)] space-y-2">
                <div className="grid grid-cols-2 gap-2 text-xs text-[var(--s-text-tertiary)] mt-2">
                  <div>指纹: {item.fingerprint}</div>
                  <div>函数: {item.function}:{item.line}</div>
                  <div>首次: {new Date(item.first_seen_at).toLocaleString('zh-CN')}</div>
                  <div>末次: {time}</div>
                  {item.org_id && <div className="col-span-2">企业: {item.org_id}</div>}
                </div>
                {item.traceback && (
                  <pre className="text-xs text-[var(--s-text-disabled)] bg-[var(--s-surface-sunken)] p-2 rounded-[var(--s-radius-control)] overflow-x-auto max-h-40 whitespace-pre-wrap">
                    {item.traceback}
                  </pre>
                )}
              </div>
            </m.div>
          )}
        </AnimatePresence>
      </Card>
    </m.div>
  );
}

function ClearConfirmModal({ isOpen, loading, onConfirm, onCancel }: {
  isOpen: boolean;
  loading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal isOpen={isOpen} onClose={onCancel} showCloseButton={false} maxWidth="max-w-sm" closeOnOverlay={!loading} closeOnEsc={!loading}>
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-warning-light rounded-full flex items-center justify-center flex-shrink-0">
          <AlertTriangle className="w-5 h-5 text-warning" />
        </div>
        <div className="flex-1">
          <h3 className="text-lg font-medium text-text-primary">确定清除已处理的错误日志？</h3>
          <p className="mt-2 text-sm text-text-tertiary">清除后，已标记为"已处理"的错误记录将被永久删除，不可恢复。</p>
        </div>
      </div>

      <div className="mt-6 flex gap-3 justify-end">
        <Button variant="secondary" size="md" onClick={onCancel} disabled={loading}>
          取消
        </Button>
        <Button
          size="md"
          loading={loading}
          onClick={onConfirm}
          className="bg-error text-text-on-accent hover:bg-error/90"
        >
          确认清除
        </Button>
      </div>
    </Modal>
  );
}
