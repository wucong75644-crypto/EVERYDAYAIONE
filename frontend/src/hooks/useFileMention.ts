/**
 * @ 文件提及 Hook
 *
 * 检测输入框中 @ 触发词，搜索工作空间文件，管理下拉选择状态。
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { searchWorkspace, type WorkspaceFileItem } from '../services/workspace';

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
  /** 是否正在搜索 */
  loading: boolean;
  /** 处理输入变化（检测 @ 触发） */
  handleInputChange: (value: string, cursorPos: number) => void;
  /** 选中文件 */
  selectFile: (file: MentionResult) => { newPrompt: string };
  /** 键盘导航（上下箭头、Enter、Escape） */
  handleKeyDown: (e: React.KeyboardEvent) => boolean;
  /** 关闭下拉 */
  close: () => void;
}

/** 从光标位置向前提取 @keyword */
function extractMentionQuery(text: string, cursorPos: number): { query: string; start: number } | null {
  // 从光标位置向前找 @
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

  // 存储当前 @ 的起始位置和光标位置
  const mentionRef = useRef<{ start: number; cursorPos: number } | null>(null);
  // 防抖定时器
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  // 请求序号（防竞态）
  const seqRef = useRef(0);

  // 清理
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const close = useCallback(() => {
    setShowDropdown(false);
    setResults([]);
    setActiveIndex(0);
    mentionRef.current = null;
  }, []);

  const handleInputChange = useCallback((value: string, cursorPos: number) => {
    const mention = extractMentionQuery(value, cursorPos);

    if (!mention) {
      close();
      return;
    }

    mentionRef.current = { start: mention.start, cursorPos };
    setShowDropdown(true);
    setActiveIndex(0);

    // 空关键词：显示空面板等待输入
    if (!mention.query) {
      setResults([]);
      setLoading(false);
      return;
    }

    // 防抖搜索
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setLoading(true);

    debounceRef.current = setTimeout(async () => {
      seqRef.current += 1;
      const seq = seqRef.current;

      try {
        const resp = await searchWorkspace(mention.query, 10);
        if (seq !== seqRef.current) return;
        setResults(resp.items.map((item) => ({
          name: item.name,
          workspace_path: (item as MentionResult).workspace_path || item.name,
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
    }, 200);
  }, [close]);

  const selectFile = useCallback((file: MentionResult): { newPrompt: string } => {
    // 返回需要替换的新 prompt（由调用方拿到 prompt 做替换）
    // 这里只返回标记，实际替换在调用方进行
    close();
    return { newPrompt: '' }; // 占位，实际在 InputControls 中处理
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
      return true; // 调用方拿 results[activeIndex] 处理
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
    selectFile,
    handleKeyDown,
    close,
  };
}
