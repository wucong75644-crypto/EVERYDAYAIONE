/**
 * 点击外部关闭的自定义 Hook
 *
 * 用于实现点击元素外部时触发关闭回调的通用逻辑
 */

import { useEffect, type RefObject } from 'react';

/**
 * 监听点击外部事件
 *
 * @param ref - 目标元素的 ref
 * @param isVisible - 是否可见（只有可见时才监听）
 * @param onClose - 关闭回调
 * @param skipCondition - 跳过关闭的条件（返回 true 时不触发关闭）
 */
export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  isVisible: boolean,
  onClose: () => void,
  skipCondition?: boolean
): void {
  useEffect(() => {
    // 不可见或满足跳过条件时不监听
    if (!isVisible || skipCondition) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [ref, isVisible, onClose, skipCondition]);
}
