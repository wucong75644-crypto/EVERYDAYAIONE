/**
 * 全局加载屏幕组件
 *
 * 用于认证初始化、路由切换等全局加载状态
 * 采用渐进式显示策略，避免短暂加载时的闪烁
 */

import { useEffect, useState } from 'react';

interface LoadingScreenProps {
  /**
   * 延迟显示时间（毫秒）
   * 默认 200ms，避免快速加载时的闪烁
   */
  delay?: number;
  /**
   * 自定义提示文本
   */
  message?: string;
}

export default function LoadingScreen({
  delay = 200,
  message = '正在加载...',
}: LoadingScreenProps) {
  const [show, setShow] = useState(delay === 0);

  useEffect(() => {
    if (delay === 0) return;

    const timer = setTimeout(() => {
      setShow(true);
    }, delay);

    return () => clearTimeout(timer);
  }, [delay]);

  if (!show) {
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center">
        {/* 旋转加载图标 */}
        <div className="inline-flex items-center justify-center w-16 h-16 mb-4">
          <div className="w-16 h-16 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin"></div>
        </div>

        {/* 加载文本 */}
        <p className="text-gray-600 text-lg font-medium">{message}</p>
      </div>
    </div>
  );
}
