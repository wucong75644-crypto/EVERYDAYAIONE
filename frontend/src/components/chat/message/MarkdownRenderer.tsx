import { lazy, memo, Suspense, useMemo } from 'react';

const RichMarkdownRenderer = lazy(() => import('./RichMarkdownRenderer'));

interface MarkdownRendererProps {
  content: string;
  isStreaming?: boolean;
  className?: string;
}

function PlainText({
  content,
  isStreaming,
  className,
}: MarkdownRendererProps) {
  const trimmed = content.replace(/\s+$/, '');
  return (
    <div className={`text-[15px] leading-relaxed whitespace-pre-wrap ${className ?? ''}`}>
      {trimmed}
      {isStreaming && content && (
        <span className="inline-block w-0.5 h-[18px] bg-text-tertiary ml-0.5 rounded-sm animate-cursor-blink" />
      )}
    </div>
  );
}

export default memo(function MarkdownRenderer(props: MarkdownRendererProps) {
  const hasMarkdown = useMemo(
    () => Boolean(props.content)
      && (/[#*`~[\]|>$-]/.test(props.content) || props.content.includes('```')),
    [props.content],
  );

  if (!hasMarkdown) return <PlainText {...props} />;

  return (
    <Suspense fallback={<PlainText {...props} />}>
      <RichMarkdownRenderer {...props} />
    </Suspense>
  );
});
