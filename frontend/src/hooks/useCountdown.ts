/**
 * 倒计时 Hook
 *
 * 提供验证码发送后的倒计时功能，自动清理定时器。
 */

import { useState, useRef, useEffect, useCallback } from 'react';

interface UseCountdownReturn {
  countdown: number;
  startCountdown: (seconds?: number) => void;
}

export function useCountdown(defaultSeconds = 60): UseCountdownReturn {
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, []);

  const startCountdown = useCallback((seconds = defaultSeconds) => {
    // 先清理可能存在的旧定时器
    if (timerRef.current) {
      clearInterval(timerRef.current);
    }

    setCountdown(seconds);
    timerRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }, [defaultSeconds]);

  return { countdown, startCountdown };
}
