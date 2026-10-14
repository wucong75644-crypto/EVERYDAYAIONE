import { useEffect, useRef, useState } from 'react';

// Smooth only the display; the store always retains all received content.
const CATCH_UP_MS = 120;
function prefix(text: string, length: number): string {
  let end = Math.min(length, text.length);
  const code = text.charCodeAt(end - 1);
  if (code >= 0xd800 && code <= 0xdbff) end += 1;
  return text.slice(0, end);
}

export function useStreamingText(content: string, streaming: boolean): string {
  const reducedMotion = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Mounts may contain a restored snapshot or an existing Markdown table.
  // Smooth subsequent deltas, never replay already received history.
  const [displayed, setDisplayed] = useState(content);
  const visible = useRef(displayed);

  useEffect(() => {
    let frame = 0;
    const publish = (text: string) => { visible.current = text; setDisplayed(text); };
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
  }, [content, streaming, reducedMotion]);

  // Completion, cancellation, snapshots and replacements never wait for animation.
  return streaming && !reducedMotion && content.startsWith(displayed) ? displayed : content;
}
