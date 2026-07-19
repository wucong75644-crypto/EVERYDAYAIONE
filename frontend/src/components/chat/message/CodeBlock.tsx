/**
 * 代码块组件
 *
 * 提供语法高亮 + 语言标签 + 一键复制功能。
 * 由 RichMarkdownRenderer 通过 react-markdown 的 components.code 映射使用。
 */

import { useState, useCallback, useRef, useEffect, memo, useMemo } from 'react';
import hljs from 'highlight.js/lib/common';
import 'highlight.js/styles/github-dark.css';

interface CodeBlockProps {
  /** 代码语言标识（如 python、typescript） */
  language?: string;
  /** 唯一可信的原始代码文本；展示高亮和复制都从它派生。 */
  rawCode: string;
}

/** 语言标识到显示名的映射 */
const LANGUAGE_DISPLAY: Record<string, string> = {
  js: 'JavaScript',
  jsx: 'JSX',
  ts: 'TypeScript',
  tsx: 'TSX',
  py: 'Python',
  python: 'Python',
  java: 'Java',
  go: 'Go',
  rust: 'Rust',
  sql: 'SQL',
  bash: 'Bash',
  sh: 'Shell',
  json: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  css: 'CSS',
  html: 'HTML',
  xml: 'XML',
  markdown: 'Markdown',
  md: 'Markdown',
  c: 'C',
  cpp: 'C++',
  csharp: 'C#',
  ruby: 'Ruby',
  php: 'PHP',
  swift: 'Swift',
  kotlin: 'Kotlin',
  dart: 'Dart',
};

function getDisplayLanguage(lang?: string): string {
  if (!lang) return '';
  return LANGUAGE_DISPLAY[lang.toLowerCase()] || lang.toUpperCase();
}

export default memo(function CodeBlock({ language, rawCode }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(rawCode);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        setCopied(false);
        timerRef.current = null;
      }, 2000);
    } catch {
      // 静默处理（Safari 兼容）
    }
  }, [rawCode]);

  const displayLang = getDisplayLanguage(language);
  const highlightedHtml = useMemo(() => {
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(rawCode, { language }).value;
      }
      return hljs.highlightAuto(rawCode).value;
    } catch {
      return null;
    }
  }, [language, rawCode]);

  return (
    <div className="rounded-lg overflow-hidden my-3 border border-border-dark">
      {/* 顶部栏：语言标签 + 复制按钮 */}
      <div className="flex items-center justify-between bg-surface-dark-card px-4 py-1.5 text-xs">
        <span className="text-text-disabled select-none">{displayLang}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-text-disabled hover:text-white transition-base p-1 rounded"
          title={copied ? '已复制' : '复制代码'}
        >
          {copied ? (
            <>
              <svg className="w-3.5 h-3.5 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span className="text-success">已复制</span>
            </>
          ) : (
            <>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      {/* highlight.js 的 value 已对源码做 HTML 转义；失败时直接渲染原始文本。 */}
      <div className="bg-surface-dark overflow-x-auto">
        <pre className="p-4 text-sm leading-relaxed m-0 whitespace-pre-wrap break-words">
          {highlightedHtml === null ? (
            <code className="hljs">{rawCode}</code>
          ) : (
            <code
              className={language ? `hljs language-${language}` : 'hljs'}
              dangerouslySetInnerHTML={{ __html: highlightedHtml }}
            />
          )}
        </pre>
      </div>
    </div>
  );
});
