import { useEffect, useState, type MutableRefObject, type RefObject } from 'react';
import { getEChartsThemeName, registerAllThemes } from '../../../constants/echartsThemes';
import type { ThemeName } from '../../../hooks/useTheme';
import { logger } from '../../../utils/logger';

export type ChartInstance = ReturnType<typeof import('echarts/core').init>;
type RenderStatus = 'idle' | 'loading' | 'ready' | 'error' | 'fallback';

let echartsPromise: Promise<typeof import('echarts/core')> | null = null;

async function loadECharts() {
  const [{ use: register }, { CanvasRenderer }, charts, components] = await Promise.all([
    import('echarts/core'),
    import('echarts/renderers'),
    import('echarts/charts'),
    import('echarts/components'),
  ]);
  register([
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
  const echarts = await import('echarts/core');
  registerAllThemes(echarts.registerTheme);
  return echarts;
}

function getECharts() {
  if (!echartsPromise) {
    echartsPromise = loadECharts().catch((error: unknown) => {
      echartsPromise = null;
      throw error;
    });
  }
  return echartsPromise;
}

export function useEChartsRender({
  containerRef,
  chartRef,
  option,
  activeType,
  theme,
  isDark,
  hasOption,
  messageId,
}: {
  containerRef: RefObject<HTMLDivElement | null>;
  chartRef: MutableRefObject<ChartInstance | null>;
  option: Record<string, unknown>;
  activeType: string;
  theme: ThemeName;
  isDark: boolean;
  hasOption: boolean;
  messageId?: string;
}) {
  const [status, setStatus] = useState<RenderStatus>('idle');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    if (!hasOption) {
      setError('图表配置为空');
      setStatus('fallback');
      return () => { disposed = true; };
    }
    setStatus('loading');
    void getECharts()
      .then((echarts) => {
        if (disposed || !containerRef.current) return;
        chartRef.current?.dispose();
        const themeName = getEChartsThemeName(theme, isDark);
        chartRef.current = echarts.init(containerRef.current, themeName);
        chartRef.current.setOption(option);
        setStatus('ready');
        setError('');
        logger.info('ChartBlock', `rendered | type=${activeType} | theme=${themeName}`);
      })
      .catch((caught: unknown) => {
        if (disposed) return;
        setError(caught instanceof Error ? caught.message : String(caught));
        setStatus('error');
        logger.error('chart:render', 'ECharts render failed', undefined, {
          messageId,
          contentType: 'chart',
          renderer: 'echarts',
          errorType: caught instanceof Error ? caught.name : typeof caught,
        });
      });
    return () => {
      disposed = true;
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [
    activeType, attempt, chartRef, containerRef, hasOption, isDark,
    messageId, option, theme,
  ]);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => chartRef.current?.resize());
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [chartRef, containerRef]);

  return {
    status,
    error,
    retry: () => {
      setStatus('loading');
      setAttempt((value) => value + 1);
    },
  };
}
