/**
 * Markdown 渲染器
 *
 * 基于 react-markdown + remark-gfm + rehype-highlight，
 * 将 AI 回复的 Markdown 文本渲染为富文本。
 *
 * 自定义组件映射：
 * - code 块 → CodeBlock（语法高亮 + 复制按钮）
 * - table → 横向滚动容器 + 缩略图检测
 * - a → 新窗口打开链接
 */

import { memo, useMemo, lazy, Suspense } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import CodeBlock from './CodeBlock';
import './markdown.css';

// highlight.js 暗色主题（按需加载，仅注册常用语言）
import 'highlight.js/styles/github-dark.css';
import 'highlight.js/lib/common';

// KaTeX 数学公式样式
import 'katex/dist/katex.min.css';

// Mermaid 图表组件（懒加载，减少首屏体积）
const MermaidBlock = lazy(() => import('./MermaidBlock'));

interface MarkdownRendererProps {
  /** Markdown 文本内容 */
  content: string;
  /** 是否正在流式输出（显示闪烁光标） */
  isStreaming?: boolean;
  /** 自定义样式类名 */
  className?: string;
}

/** 图片 URL 域名匹配（用于表格内缩略图检测） */
const IMAGE_URL_PATTERN = /^https?:\/\/.*\.(jpg|jpeg|png|webp|gif|bmp|svg)(\?.*)?$/i;
const IMAGE_CDN_DOMAINS = ['img.alicdn.com', 'img.taobao.com', 'gw.alicdn.com'];

function isImageUrl(text: string): boolean {
  const trimmed = text.trim();
  if (IMAGE_URL_PATTERN.test(trimmed)) return true;
  try {
    const url = new URL(trimmed);
    return IMAGE_CDN_DOMAINS.some((d) => url.hostname.includes(d));
  } catch {
    return false;
  }
}

/** remark/rehype 插件列表（静态，避免每次渲染重建） */
const remarkPlugins = [remarkGfm, remarkMath];
const rehypePlugins = [rehypeKatex, rehypeHighlight];

/**
 * 自定义组件映射
 *
 * react-markdown 会将 Markdown AST 映射到 React 组件，
 * 这里覆盖默认的 code/pre/table/a 渲染逻辑。
 */
const markdownComponents: Components = {
  // 代码块：区分行内代码、Mermaid 图表、普通代码块
  code({ children, className, node: _node, ...rest }) {
    const match = /language-(\w+)/.exec(className || '');
    const isInline = !match && !String(children).includes('\n');

    if (isInline) {
      return <code className={className} {...rest}>{children}</code>;
    }

    const language = match?.[1];
    const codeText = String(children).replace(/\n$/, '');

    // Mermaid 图表：懒加载渲染为 SVG
    if (language === 'mermaid') {
      return (
        <Suspense fallback={<div className="p-4 text-text-disabled text-sm">图表加载中...</div>}>
          <MermaidBlock>{codeText}</MermaidBlock>
        </Suspense>
      );
    }

    return <CodeBlock language={language}>{codeText}</CodeBlock>;
  },

  // pre：去掉默认 pre 包裹（CodeBlock 自带容器）
  pre({ children }) {
    return <>{children}</>;
  },

  // 表格：外层加横向滚动容器
  table({ children, node: _node, ...rest }) {
    return (
      <div className="markdown-table-wrapper">
        <table {...rest}>{children}</table>
      </div>
    );
  },

  // 表格单元格：检测图片 URL 并渲染为缩略图
  td({ children, node: _node, ...rest }) {
    const text = String(children ?? '').trim();

    if (isImageUrl(text)) {
      return (
        <td {...rest}>
          <img
            src={text}
            alt="缩略图"
            className="table-thumbnail"
            loading="lazy"
            onError={(e) => {
              const td = (e.target as HTMLImageElement).parentElement;
              if (td) {
                td.textContent = text;
              }
            }}
          />
        </td>
      );
    }
    return <td {...rest}>{children}</td>;
  },

  // 链接：新窗口打开
  a({ children, node: _node, ...rest }) {
    return (
      <a {...rest} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
};

export default memo(function MarkdownRenderer({
  content,
  isStreaming = false,
  className = '',
}: MarkdownRendererProps) {
  // 检测内容是否包含 Markdown 语法特征
  const hasMarkdown = useMemo(() => {
    if (!content) return false;
    // 快速检测常见 Markdown 语法（含 $ 数学公式）
    return /[#*`~\[\]|>$-]/.test(content) || content.includes('```');
  }, [content]);

  // 纯文本快速路径：无 Markdown 语法时跳过解析
  if (!hasMarkdown) {
    return (
      <div className={`text-[15px] leading-relaxed whitespace-pre-wrap ${className}`}>
        {content}
        {isStreaming && content && (
          <span className="inline-block w-2 h-4 bg-accent ml-0.5 animate-cursor-blink" />
        )}
      </div>
    );
  }

  return (
    <div className={`markdown-body ${className}`}>
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
      >
        {content}
      </Markdown>
      {isStreaming && content && (
        <span className="inline-block w-2 h-4 bg-accent ml-0.5 animate-cursor-blink" />
      )}
    </div>
  );
});
