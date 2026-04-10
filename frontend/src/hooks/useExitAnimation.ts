/**
 * 退出动画状态机 Hook（受控模式）
 *
 * 用于受控组件的"延迟卸载"场景：外部 isOpen 变为 false 时，
 * 不立即卸载 DOM，而是先播放退出动画，动画结束后才卸载。
 *
 * 与 useModalAnimation 的区别：
 * - useModalAnimation：自管理（内部 useState 控制 open/close）
 * - useExitAnimation：受控（外部 isOpen prop 驱动）
 *
 * 用于 Modal、Dropdown 等所有受控的可关闭组件。
 *
 * @example
 * ```tsx
 * function Modal({ isOpen, onClose, children }) {
 *   const { shouldRender, isClosing } = useExitAnimation(isOpen, 150);
 *   if (!shouldRender) return null;
 *   return (
 *     <div className={isClosing ? 'animate-exit' : 'animate-enter'}>
 *       {children}
 *     </div>
 *   );
 * }
 * ```
 */

import { useState, useEffect, useRef } from 'react';

interface UseExitAnimationReturn {
  /** 是否应该渲染到 DOM（包含退出动画期间） */
  shouldRender: boolean;
  /** 是否正在播放退出动画 */
  isClosing: boolean;
}

/**
 * @param isOpen - 受控的开关状态
 * @param exitDuration - 退出动画时长（毫秒），与 CSS 动画时长一致
 */
export function useExitAnimation(
  isOpen: boolean,
  exitDuration: number,
): UseExitAnimationReturn {
  const [shouldRender, setShouldRender] = useState(isOpen);
  const [isClosing, setIsClosing] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isOpen) {
      // 打开：立即渲染
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShouldRender(true);
      setIsClosing(false);
      // 取消可能在进行的退出动画
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    } else if (shouldRender) {
      // 关闭：先播放退出动画
      setIsClosing(true);
      timerRef.current = setTimeout(() => {
        setShouldRender(false);
        setIsClosing(false);
        timerRef.current = null;
      }, exitDuration);
    }
  }, [isOpen, shouldRender, exitDuration]);

  // 卸载时清理定时器
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return { shouldRender, isClosing };
}
