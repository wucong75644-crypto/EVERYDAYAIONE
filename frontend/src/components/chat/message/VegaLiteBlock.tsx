/**
 * Vega-Lite (altair) 图表渲染块
 *
 * 由 ChartBlock 在 spec_format='vegalite' 时分发,
 * 走 vega-embed (按需加载,vega 和 vega-lite 自动按需加载)。
 *
 * 详见 docs/document/TECH_沙盒产物协议.md
 */
import { useEffect, useRef, useState, memo } from 'react';
import { logger } from '../../../utils/logger';

interface VegaLiteBlockProps {
  option: Record<string, unknown>;
  title?: string;
}

let vegaEmbedPromise: Promise<typeof import('vega-embed').default> | null = null;
function getVegaEmbed() {
  if (!vegaEmbedPromise) {
    vegaEmbedPromise = import('vega-embed').then((m) => m.default);
  }
  return vegaEmbedPromise;
}

function VegaLiteBlockInner({ option, title }: VegaLiteBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;
    let viewRef: { view: { finalize: () => void } } | null = null;
    (async () => {
      try {
        const embed = await getVegaEmbed();
        if (disposed || !containerRef.current) return;
        const result = await embed(containerRef.current, option as never, {
          actions: false,
          renderer: 'svg',
        });
        viewRef = result;
        setLoading(false);
        setError(null);
      } catch (e) {
        if (!disposed) {
          logger.error('VegaLiteBlock', `init failed | ${e}`);
          setError(`Vega-Lite 渲染失败: ${e instanceof Error ? e.message : String(e)}`);
          setLoading(false);
        }
      }
    })();
    return () => {
      disposed = true;
      if (viewRef) {
        try { viewRef.view.finalize(); } catch { /* ignore */ }
      }
    };
  }, [option]);

  if (error) {
    return (
      <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
        <div className="text-sm font-medium text-error mb-2">Vega-Lite 图表渲染失败</div>
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
          <div className="text-xs text-text-tertiary">加载 Vega-Lite...</div>
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', minHeight: 400 }} />
    </div>
  );
}

export default memo(VegaLiteBlockInner);
