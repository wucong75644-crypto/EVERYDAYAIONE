/**
 * 企微扫码登录组件
 *
 * 加载企微 WwLogin JS SDK，在指定容器内渲染扫码二维码 iframe。
 * 扫码后企微会将整个页面重定向到 callback URL。
 */

import { useState, useEffect, useRef, useCallback } from 'react';
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
  const containerRef = useRef<HTMLDivElement>(null);
  const initedRef = useRef(false);

  const initQrCode = useCallback(async () => {
    if (initedRef.current) return;
    initedRef.current = true;

    try {
      // 1. 获取 QR 参数
      const qrData = await getWecomQrUrl();

      // 2. 加载 SDK（如果尚未加载）
      await loadSdk();

      // 3. 渲染二维码
      if (containerRef.current && window.WwLogin) {
        new window.WwLogin({
          id: 'wecom-qr-container',
          appid: qrData.appid,
          agentid: qrData.agentid,
          redirect_uri: encodeURIComponent(qrData.redirect_uri),
          state: qrData.state,
          lang: 'zh',
        });
      }

      setLoading(false);
    } catch {
      setError('加载企微二维码失败，请刷新重试');
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    initQrCode();
  }, [initQrCode]);

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

      {/* 二维码容器 */}
      <div
        id="wecom-qr-container"
        ref={containerRef}
        className="w-[300px] h-[400px] flex items-center justify-center"
      >
        {loading && (
          <div className="flex flex-col items-center text-gray-400">
            <svg className="animate-spin h-8 w-8 mb-2" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span className="text-sm">加载中...</span>
          </div>
        )}
      </div>

      <p className="text-sm text-gray-500 mt-2 mb-4">
        请使用企业微信 App 扫码
      </p>

      {/* 返回按钮 */}
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
