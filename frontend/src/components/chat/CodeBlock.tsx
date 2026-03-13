/**
 * 代码块组件
 *
 * 提供语法高亮 + 语言标签 + 一键复制功能。
 * 由 MarkdownRenderer 内部通过 react-markdown 的 components.code 映射使用。
 */

import { useState, useCallback, useRef, useEffect, memo } from 'react';

interface CodeBlockProps {
  /** 代码语言标识（如 python、typescript） */
  language?: string;
  /** 代码文本内容 */
  children: string;
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

export default memo(function CodeBlock({ language, children }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        setCopied(false);
        timerRef.current = null;
      }, 2000);
    } catch {
      // 静默处理（Safari 兼容）
    }
  }, [children]);

  const displayLang = getDisplayLanguage(language);

  return (
    <div className="rounded-lg overflow-hidden my-3 border border-gray-700">
      {/* 顶部栏：语言标签 + 复制按钮 */}
      <div className="flex items-center justify-between bg-gray-800 px-4 py-1.5 text-xs">
        <span className="text-gray-400 select-none">{displayLang}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-gray-400 hover:text-white transition-colors p-1 rounded"
          title={copied ? '已复制' : '复制代码'}
        >
          {copied ? (
            <>
              <svg className="w-3.5 h-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span className="text-green-400">已复制</span>
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
      {/* 代码区域：rehype-highlight 已注入高亮 class，这里只做容器 */}
      <div className="bg-gray-900 overflow-x-auto">
        <pre className="p-4 text-sm leading-relaxed m-0">
          <code className={language ? `hljs language-${language}` : 'hljs'}>
            {children}
          </code>
        </pre>
      </div>
    </div>
  );
});
