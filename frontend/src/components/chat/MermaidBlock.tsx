/**
 * Mermaid 图表渲染组件
 *
 * 将 Mermaid 语法的代码块渲染为 SVG 图表。
 * 使用 mermaid.render() 进行客户端渲染，支持流程图、时序图、甘特图等。
 */

import { useState, useEffect, useRef, useId, memo } from 'react';

interface MermaidBlockProps {
  /** Mermaid 语法文本 */
  children: string;
}

export default memo(function MermaidBlock({ children }: MermaidBlockProps) {
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const uniqueId = useId().replace(/:/g, '_');

  useEffect(() => {
    if (!children.trim()) return;

    let cancelled = false;

    async function renderDiagram() {
      try {
        const mermaid = (await import('mermaid')).default;

        mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          securityLevel: 'strict',
          fontFamily: 'ui-sans-serif, system-ui, sans-serif',
        });

        const { svg: rendered } = await mermaid.render(
          `mermaid_${uniqueId}`,
          children.trim(),
        );

        if (!cancelled) {
          setSvg(rendered);
          setError('');
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : '图表渲染失败');
          setSvg('');
        }
      }
    }

    renderDiagram();
    return () => { cancelled = true; };
  }, [children, uniqueId]);

  // 渲染失败：回退显示源码
  if (error) {
    return (
      <div className="my-3 rounded-lg border border-red-200 bg-red-50 p-4">
        <div className="text-xs text-red-500 mb-2">Mermaid 图表语法错误</div>
        <pre className="text-sm text-gray-700 whitespace-pre-wrap">{children}</pre>
      </div>
    );
  }

  // 加载中
  if (!svg) {
    return (
      <div className="my-3 flex items-center justify-center p-8 text-gray-400 text-sm">
        图表渲染中...
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="mermaid-block my-3 flex justify-center overflow-x-auto"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
});
