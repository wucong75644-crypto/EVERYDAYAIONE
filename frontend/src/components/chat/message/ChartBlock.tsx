/**
 * 交互式图表内容块
 *
 * 核心能力：
 * - 图表类型切换（柱状 ↔ 折线 ↔ 饼图），自动转换数据结构
 * - 柱状图 legend 支持单项隐藏（拆 series），折线图保持连线
 * - 工具栏：保存图片、数据视图（React 表格）、还原、全屏，合并一行
 * - 主题跟随 + 响应式 + 全屏 + 错误降级
 */

import { useRef, useEffect, useState, useCallback, useMemo, memo } from 'react';
import { useTheme } from '../../../hooks/useTheme';
import { getEChartsThemeName } from '../../../constants/echartsThemes';
import { logger } from '../../../utils/logger';

interface ChartBlockProps {
  option: Record<string, unknown>;
  title?: string;
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
// 标准化数据模型 & 类型切换
// ============================================================

type SwitchableType = 'bar' | 'line' | 'pie';
const SWITCHABLE_TYPES: SwitchableType[] = ['bar', 'line', 'pie'];
const TYPE_LABELS: Record<SwitchableType, string> = { bar: '柱状图', line: '折线图', pie: '饼图' };

interface ChartData {
  names: string[];
  values: number[][];
  seriesNames: string[];
}

function extractData(option: Record<string, unknown>): ChartData | null {
  const series = option.series as Array<Record<string, unknown>> | undefined;
  if (!series || series.length === 0) return null;

  const first = series[0];
  const firstType = first.type as string;

  if (firstType === 'pie' || firstType === 'funnel') {
    const data = first.data as Array<{ name: string; value: number }> | undefined;
    if (!data || data.length === 0) return null;
    return {
      names: data.map(d => d.name),
      values: [data.map(d => d.value)],
      seriesNames: [(first.name as string) || '数值'],
    };
  }

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

function buildOption(
  originalOption: Record<string, unknown>,
  targetType: SwitchableType,
  data: ChartData,
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

  // 柱状图 + 单 series → 拆 N 个 series（legend 控制单项显隐）
  const isSingleSeries = data.values.length === 1;
  if (isSingleSeries && targetType === 'bar') {
    return {
      title,
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', data: data.names },
      xAxis: { type: 'category', data: data.names },
      yAxis: { type: 'value' },
      series: data.names.map((name, i) => ({
        type: 'bar' as const, name, stack: 'total',
        data: data.names.map((_, j) => j === i ? (data.values[0][j] ?? 0) : null),
      })),
    };
  }

  // 折线图 / 多 series：保持原结构（折线拆 series 会断线）
  return {
    title,
    tooltip: { trigger: 'axis' },
    legend: data.seriesNames.length > 1
      ? { type: 'scroll', data: data.seriesNames } : undefined,
    xAxis: { type: 'category', data: data.names },
    yAxis: { type: 'value' },
    series: data.values.map((vals, i) => ({
      type: targetType, name: data.seriesNames[i], data: vals,
    })),
  };
}

function injectDefaults(option: Record<string, unknown>): Record<string, unknown> {
  const result = { ...option };
  result.toolbox = { show: false };
  if (!result.tooltip) {
    const series = result.series as Array<Record<string, unknown>> | undefined;
    result.tooltip = { trigger: (series?.[0]?.type === 'pie') ? 'item' : 'axis' };
  }
  return result;
}

// ============================================================
// 工具栏（React 组件，合并一行）
// ============================================================

function Toolbar({ canSwitch, activeType, onSwitch, onSave, onDataView, onRestore, onFullscreen }: {
  canSwitch: boolean;
  activeType: SwitchableType;
  onSwitch: (t: SwitchableType) => void;
  onSave: () => void;
  onDataView: () => void;
  onRestore: () => void;
  onFullscreen: () => void;
}) {
  const iconBtn = 'p-1.5 rounded-md text-text-tertiary hover:bg-hover hover:text-text-secondary transition-colors';
  return (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-1">
        {canSwitch && SWITCHABLE_TYPES.map(t => (
          <button key={t} onClick={() => onSwitch(t)}
            className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
              t === activeType
                ? 'bg-accent text-text-on-accent font-medium'
                : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'
            }`}>{TYPE_LABELS[t]}</button>
        ))}
      </div>
      <div className="flex items-center gap-0.5">
        <button onClick={onSave} className={iconBtn} title="保存图片">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </button>
        <button onClick={onDataView} className={iconBtn} title="数据视图">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="3" y1="9" x2="21" y2="9" /><line x1="3" y1="15" x2="21" y2="15" />
            <line x1="9" y1="3" x2="9" y2="21" />
          </svg>
        </button>
        {canSwitch && (
          <button onClick={onRestore} className={iconBtn} title="还原">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
          </button>
        )}
        <button onClick={onFullscreen} className={iconBtn} title="全屏查看">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// ============================================================
// 数据视图（React 表格组件，不用 dangerouslySetInnerHTML）
// ============================================================

function DataViewOverlay({ data, onClose }: { data: ChartData; onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-10 bg-surface rounded-xl border border-border-default flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border-default bg-hover">
        <span className="text-sm font-medium text-text-primary">数据视图</span>
        <button onClick={onClose}
          className="px-3 py-1 text-xs rounded-md bg-accent text-text-on-accent hover:opacity-90">关闭</button>
      </div>
      <div className="flex-1 overflow-auto p-4">
        <table className="w-full border-collapse text-sm" style={{ border: '1px solid var(--color-border-default)' }}>
          <thead>
            <tr>
              <th className="px-3 py-2 text-left font-semibold border border-border-default bg-hover text-text-primary">名称</th>
              {data.seriesNames.map((name, i) => (
                <th key={i} className="px-3 py-2 text-right font-semibold border border-border-default bg-hover text-text-primary">{name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.names.map((name, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-surface' : ''}>
                <td className="px-3 py-1.5 text-left font-medium border border-border-light text-text-primary">{name}</td>
                {data.values.map((vals, j) => (
                  <td key={j} className="px-3 py-1.5 text-right border border-border-light text-text-secondary">
                    {typeof vals[i] === 'number' ? vals[i].toLocaleString() : (vals[i] ?? '-')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

  // 稳定化 option 引用（DB JSONB 每次反序列化都是新对象）
  const optionKey = useMemo(() => JSON.stringify(option), [option]);
  const stableOption = useMemo(() => option, [optionKey]);

  // 类型切换
  const originalType = useMemo(() => getOriginalType(stableOption), [stableOption]);
  const chartData = useMemo(() => extractData(stableOption), [stableOption]);
  const canSwitch = originalType !== null && chartData !== null;
  const [activeType, setActiveType] = useState<SwitchableType>(originalType || 'bar');

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

        let opt: Record<string, unknown>;
        if (canSwitch && chartData) {
          opt = buildOption(stableOption, activeType, chartData);
        } else {
          opt = stableOption;
        }
        instance.setOption(injectDefaults(opt));

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
  }, [stableOption, activeType, theme, isDark, canSwitch, chartData]);

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

  // 保存图片
  const handleSave = useCallback(() => {
    if (!chartRef.current) return;
    const url = (chartRef.current as unknown as { getDataURL: (opts: Record<string, unknown>) => string })
      .getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || '图表'}.png`;
    a.click();
  }, [title]);

  const handleRestore = useCallback(() => setActiveType(originalType || 'bar'), [originalType]);

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

      {!loading && (
        <Toolbar canSwitch={canSwitch} activeType={activeType}
          onSwitch={setActiveType} onSave={handleSave}
          onDataView={() => setShowDataView(true)} onRestore={handleRestore}
          onFullscreen={toggleFullscreen} />
      )}

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
        {showDataView && chartData && (
          <DataViewOverlay data={chartData} onClose={() => setShowDataView(false)} />
        )}
      </div>
    </div>
  );
}

const ChartBlock = memo(ChartBlockInner);
export default ChartBlock;
