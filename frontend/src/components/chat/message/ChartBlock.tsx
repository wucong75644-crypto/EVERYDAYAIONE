/**
 * 交互式图表内容块
 *
 * 接收 ECharts option JSON，渲染交互式图表。
 * 核心能力：
 * - 图表类型切换（柱状 ↔ 折线 ↔ 饼图），自动转换数据结构
 * - toolbox：保存图片、数据视图（HTML 表格）、还原
 * - 主题跟随：6 套主题（classic/claude/linear × light/dark）
 * - 响应式 + 全屏模式
 * - 错误降级：option 无效时显示错误卡片
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
// 可切换的图表类型（只有这三种可以互相转换）
// ============================================================

type SwitchableType = 'bar' | 'line' | 'pie';
const SWITCHABLE_TYPES: SwitchableType[] = ['bar', 'line', 'pie'];
const TYPE_LABELS: Record<SwitchableType, string> = { bar: '柱状图', line: '折线图', pie: '饼图' };

/** 提取原始图表的标准化数据（名称 + 数值，不依赖图表类型） */
function extractData(option: Record<string, unknown>): { names: string[]; values: number[][]; seriesNames: string[] } | null {
  const series = option.series as Array<Record<string, unknown>> | undefined;
  if (!series || series.length === 0) return null;

  const first = series[0];
  const firstType = first.type as string;

  // 饼图/漏斗图：{name, value}[] → 提取
  if (firstType === 'pie' || firstType === 'funnel') {
    const data = first.data as Array<{ name: string; value: number }> | undefined;
    if (!data || data.length === 0) return null;
    return {
      names: data.map(d => d.name),
      values: [data.map(d => d.value)],
      seriesNames: [(first.name as string) || '数值'],
    };
  }

  // 柱状/折线图：xAxis.data + series[].data
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

/** 判断原始图表类型是否支持三种切换 */
function getOriginalType(option: Record<string, unknown>): SwitchableType | null {
  const series = option.series as Array<Record<string, unknown>> | undefined;
  if (!series || series.length === 0) return null;
  const t = series[0].type as string;
  if (SWITCHABLE_TYPES.includes(t as SwitchableType)) return t as SwitchableType;
  return null;
}

/** 将标准化数据重建为指定类型的 ECharts option */
function buildOption(
  originalOption: Record<string, unknown>,
  targetType: SwitchableType,
  data: { names: string[]; values: number[][]; seriesNames: string[] },
): Record<string, unknown> {
  // 保留原始 title
  const title = originalOption.title;

  if (targetType === 'pie') {
    // 饼图：只取第一组 series 数据
    const pieData = data.names.map((name, i) => ({ name, value: data.values[0]?.[i] ?? 0 }));
    return {
      title,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', left: 'left' },
      series: [{ type: 'pie', radius: '60%', data: pieData, name: data.seriesNames[0], label: { formatter: '{b}\n{d}%' } }],
    };
  }

  // 柱状/折线图
  return {
    title,
    tooltip: { trigger: 'axis' },
    legend: data.seriesNames.length > 1 ? { data: data.seriesNames } : undefined,
    xAxis: { type: 'category', data: data.names },
    yAxis: { type: 'value' },
    series: data.values.map((vals, i) => ({
      type: targetType,
      name: data.seriesNames[i],
      data: vals,
    })),
  };
}

// ============================================================
// dataView HTML 表格格式化
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
// 注入 toolbox（不含 magicType，切换由 React 按钮控制）
// ============================================================

function injectToolbox(
  option: Record<string, unknown>,
  data: { names: string[]; values: number[][]; seriesNames: string[] } | null,
): Record<string, unknown> {
  if (option.toolbox) return option;
  return {
    ...option,
    toolbox: {
      feature: {
        saveAsImage: { title: '保存图片' },
        dataView: {
          title: '数据视图',
          readOnly: true,
          lang: ['数据视图', '关闭', '刷新'],
          optionToContent: () => data ? optionToTable(data) : '<p>无数据</p>',
        },
        restore: { title: '还原' },
      },
      right: 16,
      top: 4,
    },
  };
}

function injectTooltip(option: Record<string, unknown>): Record<string, unknown> {
  if (option.tooltip) return option;
  const series = option.series as Array<Record<string, unknown>> | undefined;
  const firstType = series?.[0]?.type as string | undefined;
  const trigger = firstType === 'pie' ? 'item' : 'axis';
  return { ...option, tooltip: { trigger } };
}

// ============================================================
// 切换按钮组件
// ============================================================

function TypeSwitchBar({ current, original, onSwitch }: {
  current: SwitchableType;
  original: SwitchableType;
  onSwitch: (t: SwitchableType) => void;
}) {
  return (
    <div className="flex items-center gap-1 mb-2">
      {SWITCHABLE_TYPES.map(t => (
        <button
          key={t}
          onClick={() => onSwitch(t)}
          className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
            t === current
              ? 'bg-accent text-text-on-accent font-medium'
              : 'text-text-tertiary hover:bg-hover hover:text-text-secondary'
          }`}
        >
          {TYPE_LABELS[t]}{t === original ? '' : ''}
        </button>
      ))}
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

  // 图表类型切换状态
  const originalType = getOriginalType(option);
  const canSwitch = originalType !== null && extractData(option) !== null;
  const [activeType, setActiveType] = useState<SwitchableType>(originalType || 'bar');

  // 计算当前渲染用的 option
  const currentOption = useCallback(() => {
    if (!canSwitch || activeType === originalType) return option;
    const data = extractData(option);
    if (!data) return option;
    return buildOption(option, activeType, data);
  }, [option, activeType, canSwitch, originalType]);

  // 初始化 + option/type/theme 变更
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

        const opt = currentOption();
        const data = extractData(option);
        let finalOption = injectToolbox(opt, data);
        finalOption = injectTooltip(finalOption);
        instance.setOption(finalOption);

        setLoading(false);
        setError(null);
        logger.info('ChartBlock', `rendered | type=${activeType} | theme=${themeName}`);
      } catch (e) {
        if (!disposed) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg);
          setLoading(false);
          logger.error('ChartBlock', `init failed | error=${msg}`);
        }
      }
    })();

    return () => {
      disposed = true;
      if (chartRef.current) {
        chartRef.current.dispose();
        chartRef.current = null;
      }
    };
  }, [currentOption, theme, isDark, activeType, option]);

  // ResizeObserver
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

  // 类型切换
  const handleTypeSwitch = useCallback((t: SwitchableType) => {
    setActiveType(t);
  }, []);

  // 错误降级
  if (error) {
    return (
      <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
        <div className="flex items-center gap-2 text-error mb-2">
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          <span className="text-sm font-medium">图表渲染失败</span>
        </div>
        <p className="text-xs text-text-tertiary">{error}</p>
        <details className="mt-2">
          <summary className="text-xs text-text-tertiary cursor-pointer">查看原始配置</summary>
          <pre className="mt-1 text-xs bg-hover rounded p-2 overflow-auto max-h-40">
            {JSON.stringify(option, null, 2)}
          </pre>
        </details>
      </div>
    );
  }

  const wrapperClass = isFullscreen
    ? 'fixed inset-0 z-50 bg-surface flex flex-col p-4'
    : 'my-3 relative';

  return (
    <div className={wrapperClass}>
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

      {/* 图表类型切换按钮 */}
      {canSwitch && !loading && (
        <TypeSwitchBar current={activeType} original={originalType!} onSwitch={handleTypeSwitch} />
      )}

      {/* 图表容器 */}
      <div className={isFullscreen ? 'flex-1 relative' : ''}>
        {loading && (
          <div className="rounded-xl flex items-center justify-center"
            style={{ width: '100%', height: isFullscreen ? '100%' : 400, backgroundColor: 'var(--color-hover)' }}>
            <svg className="w-8 h-8 text-text-disabled animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 3v18h18" /><path d="M7 16l4-8 4 4 4-6" />
            </svg>
          </div>
        )}
        <div ref={containerRef}
          style={{ width: '100%', height: isFullscreen ? '100%' : 400, display: loading ? 'none' : 'block' }}
        />
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
