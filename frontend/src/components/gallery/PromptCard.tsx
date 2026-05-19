/**
 * 提示词卡片
 *
 * 展示单条提示词的预览图、标题、标签，点击展开详情。
 * 默认显示中文翻译，可切换查看英文原文。复制按钮始终复制英文原文。
 */

import { useState } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import { Copy, Check, ExternalLink, ChevronDown, Languages } from 'lucide-react';
import { SOFT_SPRING } from '../../utils/motion';
import type { PromptItem } from './types';

interface PromptCardProps {
  prompt: PromptItem;
}

export default function PromptCard({ prompt }: PromptCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [showEnglish, setShowEnglish] = useState(false);

  const hasZh = !!prompt.prompt_zh;
  const displayText = showEnglish || !hasZh ? prompt.prompt : prompt.prompt_zh!;

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    // 始终复制英文原文（GPT-Image-2 用英文效果最好）
    try {
      await navigator.clipboard.writeText(prompt.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = prompt.prompt;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleToggleLang = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowEnglish((v) => !v);
  };

  return (
    <m.div
      layout
      whileHover={expanded ? undefined : { y: -4, scale: 1.02 }}
      whileTap={expanded ? undefined : { scale: 0.99 }}
      transition={SOFT_SPRING}
      className="bg-surface-card rounded-xl border border-border-default shadow-sm hover:shadow-lg transition-shadow cursor-pointer overflow-hidden"
      onClick={() => setExpanded((v) => !v)}
    >
      {/* 预览图 */}
      <div className="relative aspect-square bg-hover overflow-hidden">
        {!imgLoaded && (
          <div className="absolute inset-0 animate-pulse bg-hover" />
        )}
        <img
          src={prompt.preview_url}
          alt={prompt.title}
          loading="lazy"
          onLoad={() => setImgLoaded(true)}
          className={`w-full h-full object-cover transition-opacity duration-300 ${
            imgLoaded ? 'opacity-100' : 'opacity-0'
          }`}
        />
        <span className="absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded bg-black/60 text-white">
          {prompt.aspect_ratio}
        </span>
      </div>

      {/* 信息区 */}
      <div className="p-3">
        <h3 className="text-sm font-semibold text-text-primary truncate">
          {prompt.title}
        </h3>
        <p className="text-xs text-text-tertiary mt-1 line-clamp-2">
          {prompt.description}
        </p>

        <div className="flex flex-wrap gap-1 mt-2">
          {prompt.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="text-xs px-1.5 py-0.5 rounded bg-hover text-text-tertiary"
            >
              {tag}
            </span>
          ))}
        </div>

        <div className="flex items-center justify-center mt-2">
          <m.div
            animate={{ rotate: expanded ? 180 : 0 }}
            transition={SOFT_SPRING}
          >
            <ChevronDown className="w-4 h-4 text-text-disabled" />
          </m.div>
        </div>
      </div>

      {/* 展开的详情区 */}
      <AnimatePresence>
        {expanded && (
          <m.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SOFT_SPRING}
            className="overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-t border-border-light px-3 pb-3 pt-2">
              {/* 语言切换 + 复制按钮 */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  {hasZh && (
                    <button
                      onClick={handleToggleLang}
                      className="flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-border-default hover:bg-hover transition-colors text-text-tertiary"
                    >
                      <Languages className="w-3 h-3" />
                      {showEnglish ? '看中文' : '看英文'}
                    </button>
                  )}
                </div>
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-border-default hover:bg-hover transition-colors text-text-tertiary"
                  title="复制英文原文（推荐用于生图）"
                >
                  {copied ? (
                    <>
                      <Check className="w-3 h-3 text-success" />
                      <span className="text-success">已复制</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>复制原文</span>
                    </>
                  )}
                </button>
              </div>

              {/* Prompt 文本 */}
              <pre className="text-xs text-text-secondary bg-hover rounded-lg p-3 whitespace-pre-wrap break-words max-h-60 overflow-y-auto font-mono leading-relaxed">
                {displayText}
              </pre>

              {/* 来源 */}
              <div className="flex items-center justify-between mt-2 text-xs text-text-disabled">
                <span>by {prompt.source_author}</span>
                <a
                  href={prompt.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 hover:text-accent transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  原始出处 <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </m.div>
  );
}
