/**
 * SearchPanel — 对话内消息搜索面板
 *
 * 触发方式：
 * 1. ChatHeader 顶部 🔍 按钮点击
 * 2. Cmd+F / Ctrl+F 快捷键
 *
 * 交互流程：
 * 1. 用户输入关键词
 * 2. 防抖 300ms 后调 GET /messages/search
 * 3. 渲染匹配结果列表（每条 70 字符片段 + 关键词高亮 + 时间）
 * 4. 点击某条结果 → 触发 onJumpToMessage(messageId)，由父级滚动+闪烁
 * 5. Esc 关闭面板
 *
 * 设计：
 * - 抽屉式从右侧滑出（不遮挡消息列表，方便对照）
 * - 用 framer-motion AnimatePresence + spring slide
 * - 主题感知（继承现有 token）
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import { Search, X, MessageSquare, Loader2 } from 'lucide-react';
import { searchMessages, type MessageSearchResponse } from '../../../services/message';
import { normalizeMessage } from '../../../utils/messageUtils';
import { getTextContent } from '../../../stores/useMessageStore';
import { logger } from '../../../utils/logger';
import { cn } from '../../../utils/cn';
import { FLUID_SPRING } from '../../../utils/motion';

export interface SearchPanelProps {
  /** 是否打开 */
  isOpen: boolean;
  /** 关闭回调 */
  onClose: () => void;
  /** 当前对话 ID（无对话时面板禁用） */
  conversationId: string | null;
  /** 点击搜索结果时跳转到消息 */
  onJumpToMessage: (messageId: string) => void;
}

/** 防抖间隔（ms）— 用户停止输入后多久才发起搜索 */
const SEARCH_DEBOUNCE_MS = 300;

/** 单条结果的文本片段长度（前后各取多少字符做上下文） */
const SNIPPET_LENGTH = 70;

export default function SearchPanel({
  isOpen,
  onClose,
  conversationId,
  onJumpToMessage,
}: SearchPanelProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<MessageSearchResponse['messages']>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false); // 是否已经发起过至少一次搜索
  const inputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // 打开时自动 focus + 重置状态
  useEffect(() => {
    if (isOpen) {
      // 等 framer 进场动画后 focus，避免和 motion 冲突
      const t = setTimeout(() => inputRef.current?.focus(), 100);
      return () => clearTimeout(t);
    } else {
      // 关闭时清空，避免下次打开看到旧数据
      setQuery('');
      setResults([]);
      setSearched(false);
      // 取消未完成的请求
      abortControllerRef.current?.abort();
    }
  }, [isOpen]);

  // ESC 全局关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  // 防抖搜索：query 变化后等 300ms 才真正请求
  useEffect(() => {
    if (!isOpen || !conversationId) return;
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setSearched(false);
      return;
    }

    const handle = setTimeout(async () => {
      // 取消上一次未完成的请求（避免竞态）
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      setLoading(true);
      try {
        const response = await searchMessages(conversationId, trimmed, 30, controller.signal);
        if (controller.signal.aborted) return;
        setResults(response.messages);
        setSearched(true);
      } catch (error) {
        if (controller.signal.aborted) return;
        logger.error('searchPanel', '搜索失败', error);
        setResults([]);
        setSearched(true);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(handle);
  }, [query, conversationId, isOpen]);

  const handleResultClick = useCallback(
    (messageId: string) => {
      onJumpToMessage(messageId);
      onClose();
    },
    [onJumpToMessage, onClose],
  );

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 半透明 backdrop（点击关闭，不锁滚动） */}
          <m.div
            className="fixed inset-0 z-30 bg-black/20"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
          />

          {/* 右侧抽屉 */}
          <m.aside
            className={cn(
              'fixed right-0 top-0 bottom-0 z-40',
              'w-full sm:w-[420px]',
              'bg-[var(--s-surface-overlay)]',
              'border-l border-[var(--s-border-default)]',
              'shadow-[var(--s-shadow-drop-xl)]',
              'flex flex-col',
            )}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={FLUID_SPRING}
            role="dialog"
            aria-label="搜索对话内消息"
          >
            {/* 头部：搜索框 + 关闭 */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--s-border-default)]">
              <Search className="w-4 h-4 text-[var(--s-text-tertiary)] shrink-0" aria-hidden />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索对话内消息..."
                className={cn(
                  'flex-1 bg-transparent border-0 outline-none',
                  'text-[var(--s-text-primary)]',
                  'placeholder:text-[var(--s-text-tertiary)]',
                  'text-sm',
                )}
                aria-label="搜索关键词"
              />
              {loading && <Loader2 className="w-4 h-4 animate-spin text-[var(--s-text-tertiary)]" />}
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭搜索"
                className={cn(
                  'p-1 rounded',
                  'text-[var(--s-text-tertiary)]',
                  'hover:bg-[var(--s-hover)] hover:text-[var(--s-text-primary)]',
                  'transition-colors',
                )}
              >
                <X className="w-4 h-4" aria-hidden />
              </button>
            </div>

            {/* 结果列表 */}
            <div className="flex-1 overflow-y-auto">
              <SearchResults
                query={query.trim()}
                results={results}
                loading={loading}
                searched={searched}
                onResultClick={handleResultClick}
              />
            </div>
          </m.aside>
        </>
      )}
    </AnimatePresence>
  );
}

/* ============================================================
 * SearchResults — 结果列表渲染
 * ============================================================ */

interface SearchResultsProps {
  query: string;
  results: MessageSearchResponse['messages'];
  loading: boolean;
  searched: boolean;
  onResultClick: (messageId: string) => void;
}

function SearchResults({
  query,
  results,
  loading,
  searched,
  onResultClick,
}: SearchResultsProps) {
  // 空状态
  if (!query) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-[var(--s-text-tertiary)]">
        <Search className="w-12 h-12 mb-3 opacity-30" aria-hidden />
        <p className="text-sm">输入关键词搜索对话内消息</p>
      </div>
    );
  }

  if (loading && results.length === 0) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--s-text-tertiary)]">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        <span className="text-sm">搜索中...</span>
      </div>
    );
  }

  if (searched && results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-[var(--s-text-tertiary)]">
        <MessageSquare className="w-12 h-12 mb-3 opacity-30" aria-hidden />
        <p className="text-sm">没有匹配的消息</p>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-[var(--s-border-subtle)]">
      {results.map((rawMsg) => (
        <SearchResultItem
          key={rawMsg.id}
          rawMsg={rawMsg}
          query={query}
          onClick={onResultClick}
        />
      ))}
    </ul>
  );
}

/* ============================================================
 * SearchResultItem — 单条结果，含片段和高亮
 * ============================================================ */

interface SearchResultItemProps {
  rawMsg: MessageSearchResponse['messages'][number];
  query: string;
  onClick: (messageId: string) => void;
}

function SearchResultItem({ rawMsg, query, onClick }: SearchResultItemProps) {
  // 转换为标准 Message 格式后提取文本
  const message = useMemo(() => normalizeMessage(rawMsg), [rawMsg]);
  const fullText = useMemo(() => getTextContent(message), [message]);

  // 计算关键词上下文片段
  const snippet = useMemo(() => {
    if (!fullText) return '';
    const lowerText = fullText.toLowerCase();
    const lowerQuery = query.toLowerCase();
    const idx = lowerText.indexOf(lowerQuery);
    if (idx === -1) {
      // 没找到关键词位置，返回开头片段
      return fullText.slice(0, SNIPPET_LENGTH * 2);
    }
    // 关键词前后各取 SNIPPET_LENGTH 字符
    const start = Math.max(0, idx - SNIPPET_LENGTH);
    const end = Math.min(fullText.length, idx + query.length + SNIPPET_LENGTH);
    return (start > 0 ? '...' : '') + fullText.slice(start, end) + (end < fullText.length ? '...' : '');
  }, [fullText, query]);

  // 时间格式化
  const timeStr = useMemo(() => {
    try {
      const date = new Date(message.created_at as string);
      return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return '';
    }
  }, [message.created_at]);

  // 角色标签
  const roleLabel = message.role === 'user' ? '我' : 'AI';
  const roleColorClass =
    message.role === 'user'
      ? 'text-[var(--s-accent)]'
      : 'text-[var(--s-text-secondary)]';

  return (
    <li>
      <button
        type="button"
        onClick={() => onClick(message.id)}
        className={cn(
          'w-full text-left px-4 py-3',
          'hover:bg-[var(--s-hover)] transition-colors',
          'focus:outline-none focus:bg-[var(--s-hover)]',
        )}
      >
        <div className="flex items-center gap-2 mb-1">
          <span className={cn('text-xs font-medium', roleColorClass)}>{roleLabel}</span>
          <span className="text-xs text-[var(--s-text-tertiary)]">{timeStr}</span>
        </div>
        <p className="text-sm text-[var(--s-text-primary)] leading-relaxed line-clamp-3">
          <HighlightedText text={snippet} query={query} />
        </p>
      </button>
    </li>
  );
}

/* ============================================================
 * HighlightedText — 关键词高亮
 * ============================================================ */

interface HighlightedTextProps {
  text: string;
  query: string;
}

function HighlightedText({ text, query }: HighlightedTextProps) {
  if (!query) return <>{text}</>;

  // 大小写不敏感分割，保留原文片段
  const lowerText = text.toLowerCase();
  const lowerQuery = query.toLowerCase();
  const parts: Array<{ text: string; highlighted: boolean }> = [];

  let cursor = 0;
  while (cursor < text.length) {
    const idx = lowerText.indexOf(lowerQuery, cursor);
    if (idx === -1) {
      parts.push({ text: text.slice(cursor), highlighted: false });
      break;
    }
    if (idx > cursor) {
      parts.push({ text: text.slice(cursor, idx), highlighted: false });
    }
    parts.push({ text: text.slice(idx, idx + query.length), highlighted: true });
    cursor = idx + query.length;
  }

  return (
    <>
      {parts.map((part, i) =>
        part.highlighted ? (
          <mark
            key={i}
            className="bg-[var(--s-accent-soft)] text-[var(--s-accent)] font-medium px-0.5 rounded"
          >
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}
