import DOMPurify from 'dompurify';
import { memo, useEffect, useState } from 'react';
import { logger } from '../../../utils/logger';

type RenderState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; svg: string }
  | { status: 'error'; message: string }
  | { status: 'fallback'; message: string };

interface MermaidRendererProps {
  source: string;
  messageId?: string;
}

const MAX_CACHE_ENTRIES = 50;
const svgCache = new Map<string, string>();
let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null;
let renderSequence = 0;

async function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid')
      .then((module) => module.default)
      .catch((error: unknown) => {
        mermaidPromise = null;
        throw error;
      });
  }
  return mermaidPromise;
}

function sanitizeSvg(svg: string): string {
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ['script', 'foreignObject', 'iframe', 'object', 'embed', 'a', 'image'],
  });
}

function cacheSvg(source: string, svg: string): void {
  if (svgCache.size >= MAX_CACHE_ENTRIES) {
    const oldest = svgCache.keys().next().value;
    if (oldest) svgCache.delete(oldest);
  }
  svgCache.set(source, svg);
}

async function renderSource(source: string): Promise<string> {
  const cached = svgCache.get(source);
  if (cached) return cached;

  const mermaid = await loadMermaid();
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'default',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    flowchart: { htmlLabels: false },
    suppressErrorRendering: true,
  });
  renderSequence += 1;
  const result = await mermaid.render(`mermaid_${renderSequence}`, source);
  const sanitized = sanitizeSvg(result.svg);
  if (!sanitized.trim() || !sanitized.includes('<svg')) {
    throw new Error('安全清理后没有可用的 SVG');
  }
  cacheSvg(source, sanitized);
  return sanitized;
}

function SourceFallback({ source, message }: { source: string; message: string }) {
  return (
    <div className="rounded-lg border border-error/20 bg-error-light p-4">
      <div className="mb-2 text-xs text-error">{message}</div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-sm text-text-secondary">
        {source || '（空源码）'}
      </pre>
    </div>
  );
}

export default memo(function MermaidRenderer({
  source,
  messageId,
}: MermaidRendererProps) {
  const [state, setState] = useState<RenderState>({ status: 'idle' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const normalized = source.trim();
    if (!normalized) {
      setState({ status: 'fallback', message: '关系图内容为空' });
      return () => { cancelled = true; };
    }

    setState({ status: 'loading' });
    void renderSource(normalized)
      .then((svg) => {
        if (!cancelled) setState({ status: 'ready', svg });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          status: 'error',
          message: error instanceof Error ? error.message : '关系图渲染失败',
        });
        logger.error('diagram:render', 'Mermaid render failed', undefined, {
          messageId,
          contentType: 'diagram',
          renderer: 'mermaid',
          errorType: error instanceof Error ? error.name : typeof error,
          sourceLength: normalized.length,
        });
      });
    return () => { cancelled = true; };
  }, [attempt, messageId, source]);

  if (state.status === 'idle' || state.status === 'loading') {
    return (
      <div className="flex min-h-32 items-center justify-center text-sm text-text-disabled">
        关系图加载中...
      </div>
    );
  }
  if (state.status === 'fallback') {
    return <SourceFallback source={source} message={state.message} />;
  }
  if (state.status === 'error') {
    return (
      <div>
        <SourceFallback source={source} message={`关系图渲染失败：${state.message}`} />
        <button
          type="button"
          className="mt-2 rounded-md border border-border-default px-3 py-1 text-xs text-text-secondary hover:bg-hover"
          onClick={() => setAttempt((value) => value + 1)}
        >
          重新渲染
        </button>
      </div>
    );
  }
  return (
    <div
      className="mermaid-block flex justify-center overflow-x-auto"
      data-testid="mermaid-svg"
      dangerouslySetInnerHTML={{ __html: state.svg }}
    />
  );
});
