import { useEffect, useRef, useState } from 'react';
import type { StreamingTextDisplayBaseline } from '../utils/streamingTextDisplay';

// Smooth only the display; the store always retains all received content.
const CATCH_UP_MS = 120;
function prefix(text: string, length: number): string {
  let end = Math.min(length, text.length);
  const code = text.charCodeAt(end - 1);
  if (code >= 0xd800 && code <= 0xdbff) end += 1;
  return text.slice(0, end);
}

export function useStreamingText(content: string, streaming: boolean, baseline?: StreamingTextDisplayBaseline,
  segment?: string | number, animateInitial = false): string {
  const reducedMotion = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Mounts may contain a restored snapshot or an existing Markdown table.
  // Smooth subsequent deltas, never replay already received history.
  const initialText = streaming && animateInitial ? baseline?.text ?? '' : content;
  const [display, setDisplay] = useState({ text: initialText, snapshot: baseline?.snapshot, segment });
  const visible = useRef(initialText);
  const appliedSnapshot = useRef(baseline?.snapshot);
  const appliedSegment = useRef(segment);
  const snapshot = baseline?.snapshot;
  const snapshotText = baseline?.text;

  useEffect(() => {
    let frame = 0;
    const publish = (text: string) => {
      visible.current = text;
      setDisplay({ text, snapshot: appliedSnapshot.current, segment });
    };
    if (segment !== appliedSegment.current || (snapshot && snapshot !== appliedSnapshot.current)) {
      appliedSegment.current = segment;
      appliedSnapshot.current = snapshot;
      publish(snapshot ? (snapshotText !== undefined && content.startsWith(snapshotText) ? snapshotText : content) : '');
    }
    if (!streaming || reducedMotion || !content.startsWith(visible.current) || document.hidden) {
      publish(content);
      return;
    }
    if (!visible.current && content) publish(prefix(content, 6));
    const deadline = performance.now() + CATCH_UP_MS;
    const advance = () => {
      const remaining = content.length - visible.current.length;
      if (remaining <= 0) return;
      const timeLeft = deadline - performance.now();
      const step = timeLeft <= 16 ? remaining : Math.max(2, Math.ceil(remaining * 16 / timeLeft));
      publish(prefix(content, visible.current.length + step));
      if (visible.current.length < content.length) frame = requestAnimationFrame(advance);
    };
    if (visible.current.length < content.length) frame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(frame);
  }, [content, streaming, reducedMotion, snapshot, snapshotText, segment]);

  // Completion, cancellation, snapshots and replacements never wait for animation.
  if (!streaming || reducedMotion) return content;
  if (segment !== display.segment || (snapshot && snapshot !== display.snapshot)) {
    return snapshot ? (snapshotText !== undefined && content.startsWith(snapshotText) ? snapshotText : content) : '';
  }
  return content.startsWith(display.text) ? display.text : content;
}
