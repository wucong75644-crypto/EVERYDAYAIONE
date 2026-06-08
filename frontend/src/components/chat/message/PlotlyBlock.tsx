/**
 * Plotly 图表渲染块
 *
 * 由 ChartBlock 在 spec_format='plotly' 时分发,
 * 走 plotly.js-dist-min (按需加载)。
 *
 * 详见 docs/document/TECH_沙盒产物协议.md
 */
import { useEffect, useRef, useState, memo } from 'react';
import { logger } from '../../../utils/logger';

interface PlotlyBlockProps {
  option: Record<string, unknown>;
  title?: string;
}

let plotlyPromise: Promise<typeof import('plotly.js-dist-min')> | null = null;
function getPlotly() {
  if (!plotlyPromise) {
    plotlyPromise = import('plotly.js-dist-min');
  }
  return plotlyPromise;
}

function PlotlyBlockInner({ option, title }: PlotlyBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    (async () => {
      try {
        const Plotly = await getPlotly();
        if (disposed || !containerRef.current) return;
        const data = (option.data as unknown[]) || [];
        const layout = (option.layout as Record<string, unknown>) || {};
        const config = (option.config as Record<string, unknown>) || { responsive: true };
        await Plotly.newPlot(containerRef.current, data as never, layout, config);
        setLoading(false);
        setError(null);
      } catch (e) {
        if (!disposed) {
          logger.error('PlotlyBlock', `init failed | ${e}`);
          setError(`Plotly 渲染失败: ${e instanceof Error ? e.message : String(e)}`);
          setLoading(false);
        }
      }
    })();
    return () => {
      disposed = true;
      if (containerRef.current) {
        getPlotly().then((Plotly) => {
          try { Plotly.purge(containerRef.current!); } catch { /* ignore */ }
        }).catch(() => { /* ignore */ });
      }
    };
  }, [option]);

  if (error) {
    return (
      <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
        <div className="text-sm font-medium text-error mb-2">Plotly 图表渲染失败</div>
        <p className="text-xs text-text-tertiary">{error}</p>
      </div>
    );
  }

  return (
    <div className="my-3 relative">
      {title && <div className="text-sm font-medium text-text-primary mb-2">{title}</div>}
      {loading && (
        <div className="rounded-xl flex items-center justify-center bg-hover"
             style={{ width: '100%', height: 400 }}>
          <div className="text-xs text-text-tertiary">加载 Plotly...</div>
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', minHeight: 400 }} />
    </div>
  );
}

export default memo(PlotlyBlockInner);
