/**
 * @ 文件提及 Hook
 *
 * 检测输入框中 @ 触发词，搜索工作空间文件，管理下拉选择状态。
 * 核心设计：hook 拥有 @ 位置的完整生命周期（检测 → 搜索 → 选中替换 → 关闭），
 * 调用方不需要知道 @ 在 prompt 中的位置。
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { searchWorkspace } from '../services/workspace';

export interface MentionResult {
  name: string;
  workspace_path: string;
  cdn_url: string | null;
  mime_type: string | null;
  size: number;
}

export interface UseFileMentionReturn {
  /** 是否显示下拉面板 */
  showDropdown: boolean;
  /** 搜索结果 */
  results: MentionResult[];
  /** 当前高亮索引 */
  activeIndex: number;
  /** 设置高亮索引（鼠标悬停用） */
  setActiveIndex: (index: number) => void;
  /** 是否正在搜索 */
  loading: boolean;
  /** 处理输入变化（检测 @ 触发） */
  handleInputChange: (value: string, cursorPos: number) => void;
  /**
   * 消费当前 @ 提及：从 prompt 中精准移除 @keyword 并关闭下拉。
   * 返回替换后的新 prompt 字符串。
   */
  consumeMention: (currentPrompt: string) => string;
  /** 键盘导航（上下箭头、Enter、Escape），返回 true 表示已拦截 */
  handleKeyDown: (e: React.KeyboardEvent) => boolean;
  /** 关闭下拉 */
  close: () => void;
}

/** 从光标位置向前提取 @keyword（导出供测试使用） */
export function extractMentionQuery(text: string, cursorPos: number): { query: string; start: number } | null {
  const before = text.slice(0, cursorPos);
  const atIndex = before.lastIndexOf('@');
  if (atIndex === -1) return null;

  // @ 前面必须是空白或行首
  if (atIndex > 0 && !/\s/.test(before[atIndex - 1])) return null;

  const query = before.slice(atIndex + 1);
  // query 不能包含空格（包含空格说明 @ 已结束）
  if (/\s/.test(query)) return null;

  return { query, start: atIndex };
}

export function useFileMention(): UseFileMentionReturn {
  const [showDropdown, setShowDropdown] = useState(false);
  const [results, setResults] = useState<MentionResult[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(false);

  // 精准记录当前 @ 的起始位置（供 consumeMention 使用）
  const mentionStartRef = useRef<number | null>(null);
  // 防抖定时器
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // 请求序号（防竞态）
  const seqRef = useRef(0);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const close = useCallback(() => {
    setShowDropdown(false);
    setResults([]);
    setActiveIndex(0);
    mentionStartRef.current = null;
  }, []);

  const handleInputChange = useCallback((value: string, cursorPos: number) => {
    const mention = extractMentionQuery(value, cursorPos);

    if (!mention) {
      close();
      return;
    }

    mentionStartRef.current = mention.start;
    setShowDropdown(true);
    setActiveIndex(0);

    // 防抖搜索（空关键词立即请求，有关键词防抖 200ms）
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setLoading(true);

    const doSearch = async () => {
      seqRef.current += 1;
      const seq = seqRef.current;

      try {
        const resp = await searchWorkspace(mention.query, 10);
        if (seq !== seqRef.current) return;
        setResults(resp.items.map((item) => ({
          name: item.name,
          workspace_path: item.workspace_path || item.name,
          cdn_url: item.cdn_url,
          mime_type: item.mime_type,
          size: item.size,
        })));
      } catch {
        if (seq !== seqRef.current) return;
        setResults([]);
      } finally {
        if (seq === seqRef.current) setLoading(false);
      }
    };

    if (!mention.query) {
      // 空关键词（刚输入 @）：立即请求最近文件
      doSearch();
    } else {
      // 有关键词：防抖 200ms
      debounceRef.current = setTimeout(doSearch, 200);
    }
  }, [close]);

  // 精准替换：用 mentionStartRef 定位 @keyword 在 prompt 中的确切位置
  const consumeMention = useCallback((currentPrompt: string): string => {
    const start = mentionStartRef.current;
    if (start == null) return currentPrompt;

    const before = currentPrompt.slice(0, start);
    const afterAt = currentPrompt.slice(start);
    // afterAt 以 "@keyword" 开头，找第一个空白字符作为 @keyword 的结束
    const endMatch = afterAt.match(/^@\S*/);
    const mentionLen = endMatch ? endMatch[0].length : 1;
    const after = afterAt.slice(mentionLen);

    close();
    return before + after;
  }, [close]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent): boolean => {
    if (!showDropdown || results.length === 0) return false;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => (prev + 1) % results.length);
      return true;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => (prev - 1 + results.length) % results.length);
      return true;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      return true; // 调用方读取 results[activeIndex] 做后续处理
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return true;
    }
    return false;
  }, [showDropdown, results, close]);

  return {
    showDropdown,
    results,
    activeIndex,
    setActiveIndex,
    loading,
    handleInputChange,
    consumeMention,
    handleKeyDown,
    close,
  };
}
