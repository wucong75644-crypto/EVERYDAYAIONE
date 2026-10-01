import { useCallback, useEffect, useLayoutEffect, type RefObject } from 'react';

/** Keep the native textarea and reserve space only before its first editable line. */
export function useInlineSkillLayout(
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  prefixRef: RefObject<HTMLDivElement | null>,
) {
  const syncScroll = useCallback(() => {
    if (prefixRef.current && textareaRef.current) {
      prefixRef.current.style.transform = `translateY(-${textareaRef.current.scrollTop}px)`;
    }
  }, [prefixRef, textareaRef]);

  const measure = useCallback(() => {
    const textarea = textareaRef.current;
    const prefix = prefixRef.current;
    if (!textarea || !prefix) return;
    const tags = Array.from(prefix.querySelectorAll<HTMLElement>('[data-skill-tag]'));
    const last = tags.at(-1)?.getBoundingClientRect();
    const rowTop = last?.height ? Math.max(0, last.top - prefix.getBoundingClientRect().top) : 0;
    const indent = last?.width ? Math.max(0, last.right - textarea.getBoundingClientRect().left + 8) : 0;
    textarea.style.textIndent = indent ? `${Math.ceil(indent)}px` : '';
    textarea.style.paddingTop = rowTop ? `${8 + rowTop}px` : '';
    // Extra tag rows precede the editable lines; normal messages keep the existing 5-line cap.
    const maxHeight = 120 + rowTop;
    textarea.style.maxHeight = rowTop ? `${maxHeight}px` : '';
    const scrollTop = textarea.scrollTop;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    textarea.scrollTop = scrollTop;
    syncScroll();
  }, [prefixRef, textareaRef, syncScroll]);

  // A selection can change without changing the prompt or the conversation.
  useLayoutEffect(() => { measure(); });
  useEffect(() => {
    const textarea = textareaRef.current;
    const prefix = prefixRef.current;
    if (!textarea || !prefix) return;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(prefix);
    observer?.observe(textarea.parentElement!);
    textarea.addEventListener('scroll', syncScroll);
    return () => {
      observer?.disconnect();
      textarea.removeEventListener('scroll', syncScroll);
    };
  }, [measure, prefixRef, textareaRef, syncScroll]);

  return useCallback(() => {
    if (textareaRef.current) textareaRef.current.scrollTop = 0;
    syncScroll();
  }, [textareaRef, syncScroll]);
}
