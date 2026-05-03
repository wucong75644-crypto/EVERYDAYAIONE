/**
 * 交互式图表内容块
 *
 * 接收 ECharts option JSON，在容器内渲染交互式图表。
 * 支持：tooltip、图例开关、数据缩放、导出 PNG、类型切换、全屏。
 *
 * 主题跟随：通过 useTheme 获取当前主题，传给 echarts.init(dom, themeName)。
 * 错误降级：option 无效时显示错误卡片。
 * 响应式：ResizeObserver 监听容器宽度变化。
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

/** ECharts 按需引入（动态 import，首次渲染时加载） */
async function loadECharts() {
  const [
    { use },
    { CanvasRenderer },
    charts,
    components,
  ] = await Promise.all([
    import('echarts/core'),
    import('echarts/renderers'),
    import('echarts/charts'),
    import('echarts/components'),
  ]);

  use([
    CanvasRenderer,
    charts.LineChart,
    charts.BarChart,
    charts.PieChart,
    charts.ScatterChart,
    charts.RadarChart,
    charts.HeatmapChart,
    charts.FunnelChart,
    charts.BoxplotChart,
    charts.TreemapChart,
    charts.SunburstChart,
    charts.SankeyChart,
    charts.GaugeChart,
    charts.CandlestickChart,
    components.GridComponent,
    components.TooltipComponent,
    components.LegendComponent,
    components.ToolboxComponent,
    components.DataZoomComponent,
    components.TitleComponent,
    components.VisualMapComponent,
    components.MarkLineComponent,
    components.MarkPointComponent,
    components.DatasetComponent,
  ]);

  // 注册主题
  const { registerAllThemes } = await import('../../../constants/echartsThemes');
  await registerAllThemes();

  return await import('echarts/core');
}

/** 全局缓存：ECharts 模块只加载一次 */
let echartsPromise: Promise<typeof import('echarts/core')> | null = null;
function getECharts() {
  if (!echartsPromise) {
    echartsPromise = loadECharts();
  }
  return echartsPromise;
}

/** 注入默认 toolbox 配置（用户 option 未指定时自动添加） */
function injectToolbox(option: Record<string, unknown>): Record<string, unknown> {
  if (option.toolbox) return option;
  return {
    ...option,
    toolbox: {
      feature: {
        saveAsImage: { title: '保存图片' },
        dataView: { title: '数据视图', readOnly: true },
        magicType: { type: ['line', 'bar'], title: { line: '折线图', bar: '柱状图' } },
        restore: { title: '还原' },
      },
      right: 16,
      top: 4,
    },
  };
}

/** 注入默认 tooltip 配置 */
function injectTooltip(option: Record<string, unknown>): Record<string, unknown> {
  if (option.tooltip) return option;
  return { ...option, tooltip: { trigger: 'axis' } };
}

function ChartBlockInner({ option, title, chartType }: ChartBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof import('echarts/core').init> | null>(null);
  const { theme, isDark } = useTheme();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // 初始化 + option 变更
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;

    (async () => {
      try {
        const echarts = await getECharts();
        if (disposed || !containerRef.current) return;

        // 销毁旧实例
        if (chartRef.current) {
          chartRef.current.dispose();
        }

        const themeName = getEChartsThemeName(theme, isDark);
        const instance = echarts.init(containerRef.current, themeName);
        chartRef.current = instance;

        let finalOption = injectToolbox(option);
        finalOption = injectTooltip(finalOption);
        instance.setOption(finalOption);

        setLoading(false);
        setError(null);
        logger.info('ChartBlock', `rendered | type=${chartType} | theme=${themeName}`);
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
  }, [option, theme, isDark, chartType]);

  // ResizeObserver 响应式
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => {
      chartRef.current?.resize();
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // 全屏切换
  const toggleFullscreen = useCallback(() => {
    setIsFullscreen(prev => !prev);
    // 下一帧 resize（等 DOM 更新）
    requestAnimationFrame(() => {
      chartRef.current?.resize();
    });
  }, []);

  // ESC 退出全屏
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen]);

  // 全屏后 resize
  useEffect(() => {
    if (isFullscreen) {
      requestAnimationFrame(() => chartRef.current?.resize());
    }
  }, [isFullscreen]);

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

  const containerClass = isFullscreen
    ? 'fixed inset-0 z-50 bg-surface flex flex-col p-4'
    : 'my-3 relative';

  return (
    <div className={containerClass}>
      {/* 全屏顶栏 */}
      {isFullscreen && (
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-text-primary">
            {title || '交互式图表'}
          </span>
          <button
            onClick={toggleFullscreen}
            className="p-1.5 rounded-md hover:bg-hover text-text-tertiary"
            title="退出全屏 (Esc)"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
            </svg>
          </button>
        </div>
      )}

      {/* 图表容器 */}
      <div className={isFullscreen ? 'flex-1 relative' : ''}>
        {/* 加载骨架屏 */}
        {loading && (
          <div
            className="rounded-xl flex items-center justify-center"
            style={{
              width: '100%',
              height: isFullscreen ? '100%' : 400,
              backgroundColor: 'var(--color-hover)',
            }}
          >
            <svg className="w-8 h-8 text-text-disabled animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 3v18h18" />
              <path d="M7 16l4-8 4 4 4-6" />
            </svg>
          </div>
        )}
        <div
          ref={containerRef}
          style={{
            width: '100%',
            height: isFullscreen ? '100%' : 400,
            display: loading ? 'none' : 'block',
          }}
        />
      </div>

      {/* 非全屏时的全屏按钮 */}
      {!isFullscreen && !loading && (
        <button
          onClick={toggleFullscreen}
          className="absolute top-2 right-2 p-1 rounded hover:bg-hover text-text-tertiary opacity-0 group-hover:opacity-100 transition-opacity"
          title="全屏查看"
        >
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
