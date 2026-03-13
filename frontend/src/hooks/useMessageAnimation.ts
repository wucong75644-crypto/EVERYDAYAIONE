/**
 * 消息动画管理 Hook
 *
 * 统一管理消息的进入/错误/删除动画状态，复用 useModalAnimation 的模式
 *
 * @example
 * ```tsx
 * const {
 *   entryAnimationClass,
 *   errorAnimationClass,
 *   deleteAnimationClass,
 *   triggerDeleteAnimation,
 * } = useMessageAnimation({ message });
 *
 * return (
 *   <div className={`message ${entryAnimationClass} ${errorAnimationClass} ${deleteAnimationClass}`}>
 *     ...消息内容...
 *   </div>
 * );
 * ```
 */

import { useState, useCallback, useMemo } from 'react';
import type { Message } from '../stores/useMessageStore';
import { NEW_MESSAGE_WINDOW } from '../constants/animations';

interface UseMessageAnimationOptions {
  message: Message;
  /** 是否跳过进入动画（批量加载时） */
  skipEntryAnimation?: boolean;
  /** 新消息检测时间窗口（毫秒），默认 1000 */
  newMessageWindow?: number;
}

interface UseMessageAnimationReturn {
  /** 进入动画 class */
  entryAnimationClass: string;
  /** 删除动画 class */
  deleteAnimationClass: string;
  /** 是否正在播放删除动画 */
  isDeleting: boolean;
  /** 触发删除动画 */
  triggerDeleteAnimation: () => void;
}

export function useMessageAnimation(options: UseMessageAnimationOptions): UseMessageAnimationReturn {
  const { message, skipEntryAnimation = false, newMessageWindow = NEW_MESSAGE_WINDOW } = options;

  // ==================== 进入动画检测 ====================

  /**
   * 检测是否为新消息（created_at < 1s）
   * 仅对新消息播放进入动画，避免历史消息全部播放
   */
  const isNewMessage = useMemo(() => {
    if (skipEntryAnimation) return false;

    const now = Date.now();
    const messageTime = new Date(message.created_at).getTime();
    return (now - messageTime) < newMessageWindow;
  }, [message.created_at, skipEntryAnimation, newMessageWindow]);

  /**
   * 根据消息角色和类型返回对应的进入动画 class
   * - 用户消息：无动画（即时显示，避免延迟感）
   * - AI 聊天消息（流式）：无动画（流式打字本身就是动画）
   * - AI 图片/视频消息（非流式）：淡入 + 缩放（整个出现需要过渡）
   */
  const entryAnimationClass = useMemo(() => {
    if (!isNewMessage) return '';
    if (message.role === 'user') return '';

    const genType = message.generation_params?.type;
    const isMediaMessage = genType === 'image' || genType === 'video';
    return isMediaMessage ? 'animate-ai-message-fade-scale' : '';
  }, [isNewMessage, message.role, message.generation_params?.type]);

  // ==================== 删除动画管理 ====================

  const [isDeleting, setIsDeleting] = useState(false);

  /**
   * 触发删除动画
   * 注意：不需要自动重置，因为动画完成后消息会从 DOM 移除
   */
  const triggerDeleteAnimation = useCallback(() => {
    setIsDeleting(true);
  }, []);

  const deleteAnimationClass = isDeleting ? 'animate-message-slide-out' : '';

  // ==================== 返回值 ====================

  return {
    entryAnimationClass,
    deleteAnimationClass,
    isDeleting,
    triggerDeleteAnimation,
  };
}
