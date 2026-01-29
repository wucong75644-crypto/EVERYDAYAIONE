/**
 * 消息操作工具栏组件
 *
 * 提供消息的各种操作按钮：复制、朗读、点赞/点踩、重新生成、分享、删除
 * 支持悬停显示/隐藏，包含更多菜单
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useModalAnimation } from '../../hooks/useModalAnimation';

interface MessageActionsProps {
  /** 消息 ID */
  messageId: string;
  /** 消息内容（用于复制/分享） */
  content: string;
  /** 是否为用户消息 */
  isUser: boolean;
  /** 是否为错误消息 */
  isErrorMessage: boolean;
  /** 是否正在重新生成 */
  isRegenerating: boolean;
  /** 是否正在生成中（图片/视频占位符状态） */
  isGenerating?: boolean;
  /** 工具栏是否可见 */
  visible: boolean;
  /** 重新生成回调 */
  onRegenerate?: (messageId: string) => void;
  /** 删除回调（打开确认弹框） */
  onDeleteClick?: () => void;
  /** 鼠标进入工具栏 */
  onMouseEnter: () => void;
  /** 鼠标离开工具栏 */
  onMouseLeave: () => void;
}

export default function MessageActions({
  messageId,
  content,
  isUser,
  isErrorMessage,
  isRegenerating,
  isGenerating = false,
  visible,
  onRegenerate,
  onDeleteClick,
  onMouseEnter,
  onMouseLeave,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement>(null);

  // 使用自定义 Hook 管理更多菜单动画
  const {
    isOpen: showMoreMenu,
    isClosing: moreMenuClosing,
    open: openMoreMenu,
    close: closeMoreMenu,
  } = useModalAnimation();

  // 复制功能
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('复制失败:', error);
    }
  }, [content]);

  // 朗读功能（待实现）
  const handleSpeak = useCallback(() => {
    // TODO: 朗读功能待后续迭代实现
    toast('朗读功能开发中，敬请期待', { icon: '🎧' });
  }, []);

  // 点赞/点踩功能（待实现）
  const handleFeedback = useCallback((type: 'like' | 'dislike') => {
    // TODO: 反馈功能待后续迭代实现
    const icon = type === 'like' ? '👍' : '👎';
    toast('反馈功能开发中，敬请期待', { icon });
  }, []);

  // 分享功能
  const handleShare = useCallback(async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: '分享消息',
          text: content,
        });
      } catch {
        // 用户取消分享，静默处理
      }
    } else {
      // 降级方案：复制到剪贴板
      handleCopy();
    }
  }, [content, handleCopy]);

  // 点击外部关闭更多菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (moreMenuRef.current && !moreMenuRef.current.contains(event.target as Node)) {
        if (showMoreMenu) {
          closeMoreMenu();
        }
      }
    };

    if (showMoreMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showMoreMenu]);

  return (
    <div
      className={`absolute bottom-0 ${
        isUser ? 'right-0' : 'left-0'
      } transform translate-y-full pt-1 flex items-center gap-1 transition-opacity duration-300 ${
        visible ? 'opacity-100' : 'opacity-0 pointer-events-none'
      }`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* 复制按钮 */}
      <button
        onClick={handleCopy}
        className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
        title={copied ? '已复制' : '复制'}
      >
        {copied ? (
          <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        )}
      </button>

      {/* 朗读按钮 */}
      <button
        onClick={handleSpeak}
        className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
        title="朗读"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
        </svg>
      </button>

      {/* AI 消息才显示反馈按钮 */}
      {!isUser && (
        <>
          {/* 点赞按钮 */}
          <button
            onClick={() => handleFeedback('like')}
            className="p-1.5 text-gray-500 hover:text-green-600 hover:bg-gray-100 rounded-lg transition-colors"
            title="有帮助"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5" />
            </svg>
          </button>

          {/* 点踩按钮 */}
          <button
            onClick={() => handleFeedback('dislike')}
            className="p-1.5 text-gray-500 hover:text-red-600 hover:bg-gray-100 rounded-lg transition-colors"
            title="没有帮助"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5" />
            </svg>
          </button>
        </>
      )}

      {/* 重新生成/重试按钮（所有 AI 消息显示，生成中禁用） */}
      {!isUser && onRegenerate && (
        <button
          onClick={() => onRegenerate(messageId)}
          disabled={isRegenerating || isGenerating}
          className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          title={isGenerating ? '生成中...' : isRegenerating ? '处理中...' : isErrorMessage ? '重试' : '重新生成'}
        >
          {isRegenerating ? (
            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          )}
        </button>
      )}

      {/* 分享按钮 */}
      <button
        onClick={handleShare}
        className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
        title="分享"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
        </svg>
      </button>

      {/* 更多按钮（包含下拉菜单） */}
      <div className="relative" ref={moreMenuRef}>
        <button
          onClick={() => showMoreMenu ? closeMoreMenu() : openMoreMenu()}
          className={`p-1.5 rounded-lg transition-all duration-150 ${
            showMoreMenu
              ? 'text-gray-900 bg-gray-200'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
          }`}
          title="更多"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z" />
          </svg>
        </button>

        {/* 下拉菜单 */}
        {showMoreMenu && (
          <div
            className={`absolute bottom-full right-0 mb-1.5 bg-white rounded-lg shadow-lg border border-gray-200 p-1 min-w-[100px] z-10 ${
              moreMenuClosing ? 'animate-popupExit' : 'animate-popupEnter'
            }`}
          >
            {onDeleteClick && (
              <button
                onClick={() => {
                  closeMoreMenu();
                  onDeleteClick();
                }}
                className="w-full px-3 py-1.5 text-left text-xs text-red-600 hover:bg-gray-100 rounded-md flex items-center gap-2 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5 flex-shrink-0" />
                <span>删除</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
