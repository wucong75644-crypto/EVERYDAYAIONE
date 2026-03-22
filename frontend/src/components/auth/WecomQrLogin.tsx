/**
 * 企微扫码登录组件
 *
 * 加载企微 WwLogin JS SDK，在指定容器内渲染扫码二维码 iframe。
 * 扫码后企微会将整个页面重定向到 callback URL。
 */

import { useState, useEffect, useRef } from 'react';
import { getWecomQrUrl } from '../../services/auth';

interface WecomQrLoginProps {
  /** 点击"返回密码登录"的回调 */
  onBack: () => void;
}

declare global {
  interface Window {
    WwLogin?: new (config: {
      id: string;
      appid: string;
      agentid: string;
      redirect_uri: string;
      state: string;
      href?: string;
      lang?: string;
    }) => void;
  }
}

const SDK_URL = 'https://wwcdn.weixin.qq.com/node/wework/wwopen/js/wwLogin-1.2.7.js';
const SDK_SCRIPT_ID = 'wecom-wwlogin-sdk';

export default function WecomQrLogin({ onBack }: WecomQrLoginProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const wrapperRef = useRef<HTMLDivElement>(null);
  const initedRef = useRef(false);
  // 每个实例用唯一 ID，避免多个组件冲突
  const containerId = useRef(`wecom-qr-${Date.now()}`);

  useEffect(() => {
    if (initedRef.current) return;
    initedRef.current = true;

    let cancelled = false;

    (async () => {
      try {
        const qrData = await getWecomQrUrl();
        await loadSdk();

        if (cancelled || !wrapperRef.current) return;

        // 手动创建 SDK 容器（React 不管理其子节点，避免 removeChild 冲突）
        const sdkDiv = document.createElement('div');
        sdkDiv.id = containerId.current;
        wrapperRef.current.appendChild(sdkDiv);

        if (window.WwLogin) {
          new window.WwLogin({
            id: containerId.current,
            appid: qrData.appid,
            agentid: qrData.agentid,
            redirect_uri: encodeURIComponent(qrData.redirect_uri),
            state: qrData.state,
            lang: 'zh',
          });
        }

        if (!cancelled) setLoading(false);
      } catch {
        if (!cancelled) {
          setError('加载企微二维码失败，请刷新重试');
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      // 卸载时清理 SDK 创建的 DOM
      if (wrapperRef.current) {
        wrapperRef.current.innerHTML = '';
      }
    };
  }, []);

  return (
    <div className="flex flex-col items-center">
      <h3 className="text-base font-medium text-gray-900 mb-3">
        企业微信扫码登录
      </h3>

      {error && (
        <div className="w-full bg-red-50 text-red-600 p-2.5 rounded-lg text-sm mb-3">
          {error}
        </div>
      )}

      {/* loading 状态（独立于 SDK 容器） */}
      {loading && (
        <div className="w-[300px] h-[400px] flex items-center justify-center">
          <div className="flex flex-col items-center text-gray-400">
            <svg className="animate-spin h-8 w-8 mb-2" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span className="text-sm">加载中...</span>
          </div>
        </div>
      )}

      {/* SDK 容器（React 不管理子节点，由 SDK 直接操作 DOM） */}
      <div
        ref={wrapperRef}
        className={loading ? 'hidden' : 'w-[300px] h-[400px]'}
      />

      <p className="text-sm text-gray-500 mt-2 mb-4">
        请使用企业微信 App 扫码
      </p>

      <button
        type="button"
        onClick={onBack}
        className="text-sm text-blue-600 hover:text-blue-500 transition-colors"
      >
        返回密码登录
      </button>
    </div>
  );
}

/**
 * 动态加载企微 WwLogin JS SDK
 */
function loadSdk(): Promise<void> {
  return new Promise((resolve, reject) => {
    // 已加载
    if (window.WwLogin) {
      resolve();
      return;
    }

    // 正在加载
    const existing = document.getElementById(SDK_SCRIPT_ID);
    if (existing) {
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () => reject(new Error('SDK load failed')));
      return;
    }

    // 首次加载
    const script = document.createElement('script');
    script.id = SDK_SCRIPT_ID;
    script.src = SDK_URL;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('SDK load failed'));
    document.head.appendChild(script);
  });
}
