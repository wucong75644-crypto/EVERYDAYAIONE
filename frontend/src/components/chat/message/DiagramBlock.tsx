import { memo, useEffect, useRef, useState } from 'react';
import type { DiagramPart } from '../../../types/message';
import { logger } from '../../../utils/logger';
import MermaidRenderer from './MermaidRenderer';

interface DiagramBlockProps {
  diagram: DiagramPart;
  messageId: string;
}

export default memo(function DiagramBlock({
  diagram,
  messageId,
}: DiagramBlockProps) {
  const [copied, setCopied] = useState(false);
  const mountedRef = useRef(true);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    mountedRef.current = false;
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
    }
  }, []);

  async function copySource(): Promise<void> {
    try {
      await navigator.clipboard.writeText(diagram.source);
      if (!mountedRef.current) return;
      setCopied(true);
      resetTimerRef.current = window.setTimeout(() => {
        if (mountedRef.current) setCopied(false);
      }, 1500);
    } catch (error: unknown) {
      logger.error('diagram:copy', 'Copy Mermaid source failed', error, {
        messageId,
        contentType: 'diagram',
      });
    }
  }

  return (
    <section className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
      <header className="mb-3 flex items-center justify-between gap-3">
        <h3 className="min-w-0 truncate text-sm font-medium text-text-primary">
          {diagram.title || '关系图'}
        </h3>
        <button
          type="button"
          className="shrink-0 rounded-md px-2 py-1 text-xs text-text-tertiary hover:bg-hover"
          onClick={() => void copySource()}
        >
          {copied ? '已复制' : '复制源码'}
        </button>
      </header>
      <MermaidRenderer source={diagram.source} messageId={messageId} />
    </section>
  );
});
