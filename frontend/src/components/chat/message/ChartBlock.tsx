/**
 * 交互式图表内容块
 *
 * 核心能力：
 * - 图表类型切换（柱状 ↔ 折线 ↔ 饼图），自动转换数据结构
 * - 所有图表类型均支持 legend 点击隐藏单项
 * - 工具栏：保存图片、数据视图（HTML 表格）、还原，与切换按钮同行
 * - 主题跟随 + 响应式 + 全屏 + 错误降级
 */

import { useRef, useEffect, useState, useCallback, memo } from 'react';
import { useTheme } from '../../../hooks/useTheme';
import { getEChartsThemeName } from '../../../constants/echartsThemes';
import { logger } from '../../../utils/logger';

interface ChartBlockProps {
  option: Record<string, unknown>;
  title?: string;
  chartType?: string;
}

// ============================================================
// ECharts 按需加载（全局单例）
// ============================================================

async function loadECharts() {
  const [{ use }, { CanvasRenderer }, charts, components] = await Promise.all([
    import('echarts/core'),
    import('echarts/renderers'),
    import('echarts/charts'),
    import('echarts/components'),
  ]);
  use([
    CanvasRenderer,
    charts.LineChart, charts.BarChart, charts.PieChart, charts.ScatterChart,
    charts.RadarChart, charts.HeatmapChart, charts.FunnelChart, charts.BoxplotChart,
    charts.TreemapChart, charts.SunburstChart, charts.SankeyChart,
    charts.GaugeChart, charts.CandlestickChart,
    components.GridComponent, components.TooltipComponent, components.LegendComponent,
    components.ToolboxComponent, components.DataZoomComponent, components.TitleComponent,
    components.VisualMapComponent, components.MarkLineComponent,
    components.MarkPointComponent, components.DatasetComponent,
  ]);
  const { registerAllThemes } = await import('../../../constants/echartsThemes');
  await registerAllThemes();
  return await import('echarts/core');
}

let echartsPromise: Promise<typeof import('echarts/core')> | null = null;
function getECharts() {
  if (!echartsPromise) echartsPromise = loadECharts();
  return echartsPromise;
}

// ============================================================
// 类型切换 & 数据转换
// ============================================================

type SwitchableType = 'bar' | 'line' | 'pie';
const SWITCHABLE_TYPES: SwitchableType[] = ['bar', 'line', 'pie'];
const TYPE_LABELS: Record<SwitchableType, string> = { bar: '柱状图', line: '折线图', pie: '饼图' };

/** 从任意 option 中提取标准化数据 */
function extractData(option: Record<string, unknown>) {
  const series = option.series as Array<Record<string, unknown>> | undefined;
  if (!series || series.length === 0) return null;

  const first = series[0];
  const firstType = first.type as string;

  // 饼图/漏斗图：{name, value}[]
  if (firstType === 'pie' || firstType === 'funnel') {
    const data = first.data as Array<{ name: string; value: number }> | undefined;
    if (!data || data.length === 0) return null;
    return {
      names: data.map(d => d.name),
      values: [data.map(d => d.value)],
      seriesNames: [(first.name as string) || '数值'],
    };
  }

  // 柱状/折线：xAxis.data + series[].data
  const xAxis = option.xAxis as Record<string, unknown> | Array<Record<string, unknown>> | undefined;
  const categories = Array.isArray(xAxis)
    ? (xAxis[0]?.data as string[] | undefined)
    : (xAxis?.data as string[] | undefined);
  if (!categories || categories.length === 0) return null;

  return {
    names: categories,
    values: series.map(s => (s.data as number[]) || []),
    seriesNames: series.map(s => (s.name as string) || '数值'),
  };
}

function getOriginalType(option: Record<string, unknown>): SwitchableType | null {
  const series = option.series as Array<Record<string, unknown>> | undefined;
  if (!series || series.length === 0) return null;
  const t = series[0].type as string;
  return SWITCHABLE_TYPES.includes(t as SwitchableType) ? (t as SwitchableType) : null;
}

/** 构建目标类型的 option（所有类型均带 legend 支持单项隐藏） */
function buildOption(
  originalOption: Record<string, unknown>,
  targetType: SwitchableType,
  data: { names: string[]; values: number[][]; seriesNames: string[] },
): Record<string, unknown> {
  const title = originalOption.title;

  if (targetType === 'pie') {
    const pieData = data.names.map((name, i) => ({ name, value: data.values[0]?.[i] ?? 0 }));
    return {
      title,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { type: 'scroll', orient: 'vertical', left: 'left', top: 'middle' },
      series: [{
        type: 'pie', radius: '60%', data: pieData,
        name: data.seriesNames[0],
        label: { formatter: '{b}\n{d}%' },
      }],
    };
  }

  // 柱状/折线：每个名称拆成独立 series → legend 可控制单项显隐
  const isSingleSeries = data.values.length === 1;
  if (isSingleSeries) {
    // 单 series → 拆成 N 个 series（每个平台一个），共享 xAxis
    const seriesList = data.names.map((name, i) => ({
      type: targetType,
      name,
      data: data.names.map((_, j) => j === i ? (data.values[0][j] ?? 0) : null),
      // 柱状图：相同位置堆叠成一根柱子
      ...(targetType === 'bar' ? { stack: 'total' } : {}),
    }));
    return {
      title,
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', data: data.names },
      xAxis: { type: 'category', data: data.names },
      yAxis: { type: 'value' },
      series: seriesList,
    };
  }

  // 多 series：保持原结构，legend 控制各 series 显隐
  return {
    title,
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', data: data.seriesNames },
    xAxis: { type: 'category', data: data.names },
    yAxis: { type: 'value' },
    series: data.values.map((vals, i) => ({
      type: targetType, name: data.seriesNames[i], data: vals,
    })),
  };
}

// ============================================================
// dataView 表格
// ============================================================

function optionToTable(data: { names: string[]; values: number[][]; seriesNames: string[] }): string {
  const s = {
    table: 'width:100%;border-collapse:collapse;font-size:13px;border:1px solid #d1d5db;',
    th: 'padding:8px 14px;text-align:left;border:1px solid #d1d5db;font-weight:600;color:#111827;background:#f3f4f6;',
    thR: 'padding:8px 14px;text-align:right;border:1px solid #d1d5db;font-weight:600;color:#111827;background:#f3f4f6;',
    td: 'padding:6px 14px;text-align:right;border:1px solid #e5e7eb;',
    tdL: 'padding:6px 14px;text-align:left;border:1px solid #e5e7eb;font-weight:500;',
    even: 'background:#f9fafb;',
  };
  let html = `<table style="${s.table}"><thead><tr><th style="${s.th}">名称</th>`;
  for (const name of data.seriesNames) html += `<th style="${s.thR}">${name}</th>`;
  html += '</tr></thead><tbody>';
  for (let i = 0; i < data.names.length; i++) {
    const bg = i % 2 === 0 ? s.even : '';
    html += `<tr style="${bg}"><td style="${s.tdL}">${data.names[i]}</td>`;
    for (const vals of data.values) {
      const v = vals[i];
      html += `<td style="${s.td}">${typeof v === 'number' ? v.toLocaleString() : (v ?? '-')}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  return html;
}

// ============================================================
// toolbox 注入（隐藏 ECharts 原生 toolbox，由 React 工具栏统一渲染）
// ============================================================

function injectDefaults(option: Record<string, unknown>): Record<string, unknown> {
  const result = { ...option };
  // 隐藏 ECharts 原生 toolbox（由 React 工具栏替代）
  result.toolbox = { show: false };
  // tooltip
  if (!result.tooltip) {
    const series = result.series as Array<Record<string, unknown>> | undefined;
    const firstType = series?.[0]?.type as string | undefined;
    result.tooltip = { trigger: firstType === 'pie' ? 'item' : 'axis' };
  }
  return result;
}

// ============================================================
// 工具栏（类型切换 + 保存/数据视图/还原 合并一行）
// ============================================================

function Toolbar({ canSwitch, activeType, originalType, onSwitch, onSave, onDataView, onRestore }: {
  canSwitch: boolean;
  activeType: SwitchableType;
  originalType: SwitchableType | null;
  onSwitch: (t: SwitchableType) => void;
  onSave: () => void;
  onDataView: () => void;
  onRestore: () => void;
}) {
  const iconBtn = 'p-1.5 rounded-md text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors';
  return (
    <div className="flex items-center justify-between mb-2">
      {/* 左侧：类型切换 */}
      <div className="flex items-center gap-1">
        {canSwitch && SWITCHABLE_TYPES.map(t => (
          <button key={t} onClick={() => onSwitch(t)}
            className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
              t === activeType
                ? 'bg-accent text-text-on-accent font-medium'
                : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'
            }`}
          >
            {TYPE_LABELS[t]}
          </button>
        ))}
      </div>
      {/* 右侧：工具按钮 */}
      <div className="flex items-center gap-0.5">
        <button onClick={onSave} className={iconBtn} title="保存图片">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </button>
        <button onClick={onDataView} className={iconBtn} title="数据视图">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="3" y1="9" x2="21" y2="9" />
            <line x1="3" y1="15" x2="21" y2="15" />
            <line x1="9" y1="3" x2="9" y2="21" />
          </svg>
        </button>
        {canSwitch && (
          <button onClick={onRestore} className={iconBtn} title="还原">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 数据视图弹层
// ============================================================

function DataViewOverlay({ html, onClose }: { html: string; onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-10 bg-surface rounded-xl border border-border-default flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-default bg-hover">
        <span className="text-sm font-medium text-text-primary">数据视图</span>
        <button onClick={onClose}
          className="px-3 py-1 text-xs rounded-md bg-accent text-text-on-accent hover:opacity-90">
          关闭
        </button>
      </div>
      <div className="flex-1 overflow-auto p-4" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================

function ChartBlockInner({ option, title }: ChartBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof import('echarts/core').init> | null>(null);
  const { theme, isDark } = useTheme();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showDataView, setShowDataView] = useState(false);

  // 类型切换
  const originalType = getOriginalType(option);
  const canSwitch = originalType !== null && extractData(option) !== null;
  const [activeType, setActiveType] = useState<SwitchableType>(originalType || 'bar');

  const currentOption = useCallback(() => {
    const data = extractData(option);
    if (!canSwitch || !data) return option;
    // 始终用 buildOption 重建（确保 legend 完整）
    return buildOption(option, activeType, data);
  }, [option, activeType, canSwitch]);

  // 渲染
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    (async () => {
      try {
        const echarts = await getECharts();
        if (disposed || !containerRef.current) return;
        if (chartRef.current) chartRef.current.dispose();

        const themeName = getEChartsThemeName(theme, isDark);
        const instance = echarts.init(containerRef.current, themeName);
        chartRef.current = instance;

        const finalOption = injectDefaults(currentOption());
        instance.setOption(finalOption);

        setLoading(false);
        setError(null);
        logger.info('ChartBlock', `rendered | type=${activeType} | theme=${themeName}`);
      } catch (e) {
        if (!disposed) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
          logger.error('ChartBlock', `init failed | ${e}`);
        }
      }
    })();
    return () => { disposed = true; if (chartRef.current) { chartRef.current.dispose(); chartRef.current = null; } };
  }, [currentOption, theme, isDark, activeType]);

  // Resize
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => chartRef.current?.resize());
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // 全屏
  const toggleFullscreen = useCallback(() => {
    setIsFullscreen(prev => !prev);
    requestAnimationFrame(() => chartRef.current?.resize());
  }, []);
  useEffect(() => {
    if (!isFullscreen) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') setIsFullscreen(false); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [isFullscreen]);
  useEffect(() => {
    if (isFullscreen) requestAnimationFrame(() => chartRef.current?.resize());
  }, [isFullscreen]);

  // 工具栏操作
  const handleSave = useCallback(() => {
    if (!chartRef.current) return;
    const url = (chartRef.current as unknown as { getDataURL: (opts: Record<string, unknown>) => string })
      .getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || '图表'}.png`;
    a.click();
  }, [title]);

  const handleRestore = useCallback(() => {
    setActiveType(originalType || 'bar');
  }, [originalType]);

  const dataViewHtml = useCallback(() => {
    const data = extractData(option);
    return data ? optionToTable(data) : '<p>无数据</p>';
  }, [option]);

  // 错误降级
  if (error) {
    return (
      <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
        <div className="flex items-center gap-2 text-error mb-2">
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          <span className="text-sm font-medium">图表渲染失败</span>
        </div>
        <p className="text-xs text-text-tertiary">{error}</p>
        <details className="mt-2">
          <summary className="text-xs text-text-tertiary cursor-pointer">查看原始配置</summary>
          <pre className="mt-1 text-xs bg-hover rounded p-2 overflow-auto max-h-40">{JSON.stringify(option, null, 2)}</pre>
        </details>
      </div>
    );
  }

  return (
    <div className={isFullscreen ? 'fixed inset-0 z-50 bg-surface flex flex-col p-4' : 'my-3 relative'}>
      {/* 全屏顶栏 */}
      {isFullscreen && (
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-text-primary">{title || '交互式图表'}</span>
          <button onClick={toggleFullscreen} className="p-1.5 rounded-md hover:bg-hover text-text-tertiary" title="退出全屏 (Esc)">
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
            </svg>
          </button>
        </div>
      )}

      {/* 工具栏（类型切换 + 保存/数据视图/还原 同一行） */}
      {!loading && (
        <Toolbar
          canSwitch={canSwitch} activeType={activeType} originalType={originalType}
          onSwitch={setActiveType} onSave={handleSave}
          onDataView={() => setShowDataView(true)} onRestore={handleRestore}
        />
      )}

      {/* 图表容器 */}
      <div className={`relative ${isFullscreen ? 'flex-1' : ''}`}>
        {loading && (
          <div className="rounded-xl flex items-center justify-center"
            style={{ width: '100%', height: isFullscreen ? '100%' : 400, backgroundColor: 'var(--color-hover)' }}>
            <svg className="w-8 h-8 text-text-disabled animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 3v18h18" /><path d="M7 16l4-8 4 4 4-6" />
            </svg>
          </div>
        )}
        <div ref={containerRef}
          style={{ width: '100%', height: isFullscreen ? '100%' : 400, display: loading ? 'none' : 'block' }} />

        {/* 数据视图弹层（覆盖图表区域） */}
        {showDataView && <DataViewOverlay html={dataViewHtml()} onClose={() => setShowDataView(false)} />}
      </div>

      {/* 全屏按钮 */}
      {!isFullscreen && !loading && (
        <button onClick={toggleFullscreen}
          className="absolute top-2 right-2 p-1 rounded hover:bg-hover text-text-tertiary opacity-0 group-hover:opacity-100 transition-opacity"
          title="全屏查看">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          </svg>
        </button>
      )}
    </div>
  );
}

const ChartBlock = memo(ChartBlockInner);
export default ChartBlock;
