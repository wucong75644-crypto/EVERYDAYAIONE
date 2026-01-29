/**
 * 模态框动画管理 Hook
 *
 * 统一管理模态框的打开/关闭动画状态，避免重复代码
 *
 * @example
 * ```tsx
 * const { isOpen, isClosing, open, close } = useModalAnimation();
 *
 * return (
 *   <div className={isClosing ? 'animate-exit' : 'animate-enter'}>
 *     {isOpen && <Modal onClose={close} />}
 *   </div>
 * );
 * ```
 */

import { useState, useCallback } from 'react';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../constants/animations';

interface UseModalAnimationOptions {
  /** 动画持续时间（毫秒），默认使用 MODAL_CLOSE_ANIMATION_DURATION */
  duration?: number;
  /** 关闭动画完成后的回调 */
  onClosed?: () => void;
}

interface UseModalAnimationReturn {
  /** 模态框是否打开 */
  isOpen: boolean;
  /** 是否正在执行关闭动画 */
  isClosing: boolean;
  /** 打开模态框 */
  open: () => void;
  /** 关闭模态框（带动画） */
  close: () => void;
}

export function useModalAnimation(options: UseModalAnimationOptions = {}): UseModalAnimationReturn {
  const { duration = MODAL_CLOSE_ANIMATION_DURATION, onClosed } = options;

  const [isOpen, setIsOpen] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  const open = useCallback(() => {
    setIsOpen(true);
    setIsClosing(false);
  }, []);

  const close = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsOpen(false);
      setIsClosing(false);
      onClosed?.();
    }, duration);
  }, [duration, onClosed]);

  return {
    isOpen,
    isClosing,
    open,
    close,
  };
}
