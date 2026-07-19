import { useEffect, useMemo, useState, type MutableRefObject, type RefObject } from 'react';
import { getEChartsThemeName } from '../../../constants/echartsThemes';
import type { ThemeName } from '../../../hooks/useTheme';
import { logger } from '../../../utils/logger';

export type ChartInstance = ReturnType<typeof import('./echartsRuntime').init>;
type RenderStatus = 'idle' | 'loading' | 'ready' | 'error' | 'fallback';
type RenderResult = {
  key: object;
  status: RenderStatus;
  error: string;
};

let echartsPromise: Promise<typeof import('./echartsRuntime')> | null = null;

function getECharts() {
  if (!echartsPromise) {
    echartsPromise = import('./echartsRuntime')
      .catch((error: unknown) => {
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
  const [attempt, setAttempt] = useState(0);
  const renderKey = useMemo(() => ({
    activeType,
    attempt,
    hasOption,
    isDark,
    messageId,
    option,
    theme,
  }), [activeType, attempt, hasOption, isDark, messageId, option, theme]);
  const [result, setResult] = useState<RenderResult>(() => ({
    key: renderKey,
    status: 'idle',
    error: '',
  }));

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    if (!hasOption) {
      return () => { disposed = true; };
    }
    void getECharts()
      .then((echarts) => {
        if (disposed || !containerRef.current) return;
        chartRef.current?.dispose();
        const themeName = getEChartsThemeName(theme, isDark);
        chartRef.current = echarts.init(containerRef.current, themeName);
        chartRef.current.setOption(option);
        setResult({ key: renderKey, status: 'ready', error: '' });
        logger.info('ChartBlock', `rendered | type=${activeType} | theme=${themeName}`);
      })
      .catch((caught: unknown) => {
        if (disposed) return;
        setResult({
          key: renderKey,
          status: 'error',
          error: caught instanceof Error ? caught.message : String(caught),
        });
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
  }, [activeType, chartRef, containerRef, hasOption, isDark, messageId, option, renderKey, theme]);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => chartRef.current?.resize());
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [chartRef, containerRef]);

  const status = result.key === renderKey ? result.status : 'loading';
  return {
    status: hasOption ? status : 'fallback',
    error: hasOption ? result.error : '图表配置为空',
    retry: () => {
      setAttempt((value) => value + 1);
    },
  };
}
