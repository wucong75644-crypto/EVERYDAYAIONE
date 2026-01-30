/**
 * 登录页面
 *
 * 支持两种登录方式：
 * 1. 手机号 + 密码（默认）
 * 2. 手机号 + 验证码（Tab 切换）
 */

import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { sendCode, loginByPhone, loginByPassword } from '../services/auth';
import type { ApiErrorResponse } from '../types/auth';
import { AxiosError } from 'axios';
import Footer from '../components/Footer';

type LoginMode = 'password' | 'code';

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setUser, setToken, isAuthenticated } = useAuthStore();

  // 获取重定向目标路径（由 ProtectedRoute 传递）
  const from = (location.state as { from?: string })?.from || '/chat';

  const [loginMode, setLoginMode] = useState<LoginMode>('password');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [error, setError] = useState('');

  // 焦点循环 refs
  const phoneRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);

  // Tab 键焦点循环处理
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab') return;

    const activeElement = document.activeElement;

    if (e.shiftKey && activeElement === phoneRef.current) {
      // Shift+Tab 在第一个元素，跳到最后一个
      e.preventDefault();
      submitRef.current?.focus();
    } else if (!e.shiftKey && activeElement === submitRef.current) {
      // Tab 在最后一个元素，跳到第一个
      e.preventDefault();
      phoneRef.current?.focus();
    }
  };

  // 已登录用户重定向到原页面或聊天页
  useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, from]);

  // 自动填充上次登录的手机号
  useEffect(() => {
    const lastPhone = localStorage.getItem('last_login_phone');
    if (lastPhone) {
      setPhone(lastPhone);
    }
  }, []);

  const validatePhone = (phone: string): boolean => {
    return /^1[3-9]\d{9}$/.test(phone);
  };

  const handleSendCode = async () => {
    if (!validatePhone(phone)) {
      setError('请输入正确的手机号');
      return;
    }

    setSendingCode(true);
    setError('');

    try {
      await sendCode({ phone, purpose: 'login' });

      // 开始倒计时
      setCountdown(60);
      const timer = setInterval(() => {
        setCountdown((prev) => {
          if (prev <= 1) {
            clearInterval(timer);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '发送验证码失败');
    } finally {
      setSendingCode(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validatePhone(phone)) {
      setError('请输入正确的手机号');
      return;
    }

    if (loginMode === 'password' && !password) {
      setError('请输入密码');
      return;
    }

    if (loginMode === 'code' && !code) {
      setError('请输入验证码');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const response =
        loginMode === 'password'
          ? await loginByPassword({ phone, password })
          : await loginByPhone({ phone, code });

      // 保存 token 和用户信息
      setToken(response.token.access_token);
      setUser(response.user);

      // 记住手机号
      localStorage.setItem('last_login_phone', phone);

      // 跳转到原页面或聊天页（replace: true 避免回退到登录页）
      navigate(from, { replace: true });
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '登录失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <Link to="/" tabIndex={-1} className="flex justify-center">
          <span className="text-3xl font-bold text-blue-600">每日AI</span>
        </Link>
        <h2 className="mt-6 text-center text-2xl font-bold text-gray-900">
          用户登录
        </h2>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow-lg sm:rounded-lg sm:px-10">
          {/* 登录方式切换 */}
          <div className="flex border-b border-gray-200 mb-6">
            <button
              type="button"
              tabIndex={-1}
              className={`flex-1 py-2 text-center font-medium transition-colors ${
                loginMode === 'password'
                  ? 'text-blue-600 border-b-2 border-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => {
                setLoginMode('password');
                setError('');
              }}
            >
              密码登录
            </button>
            <button
              type="button"
              tabIndex={-1}
              className={`flex-1 py-2 text-center font-medium transition-colors ${
                loginMode === 'code'
                  ? 'text-blue-600 border-b-2 border-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => {
                setLoginMode('code');
                setError('');
              }}
            >
              验证码登录
            </button>
          </div>

          {/* 表单 */}
          <form onSubmit={handleSubmit} onKeyDown={handleKeyDown} className="space-y-5">
            {error && (
              <div className="bg-red-50 text-red-600 p-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            {/* 手机号 */}
            <div>
              <label
                htmlFor="phone"
                className="block text-sm font-medium text-gray-700"
              >
                手机号
              </label>
              <input
                ref={phoneRef}
                id="phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                placeholder="请输入手机号"
                maxLength={11}
              />
            </div>

            {/* 密码登录模式 */}
            {loginMode === 'password' && (
              <div>
                <label
                  htmlFor="password"
                  className="block text-sm font-medium text-gray-700"
                >
                  密码
                </label>
                <div className="mt-1 relative">
                  <input
                    ref={passwordRef}
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="block w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 pr-10"
                    placeholder="请输入密码"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? (
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                      </svg>
                    ) : (
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                      </svg>
                    )}
                  </button>
                </div>
                <div className="mt-1 text-right">
                  <Link
                    to="/forgot-password"
                    tabIndex={-1}
                    className="text-sm text-blue-600 hover:text-blue-500"
                  >
                    忘记密码？
                  </Link>
                </div>
              </div>
            )}

            {/* 验证码登录模式 */}
            {loginMode === 'code' && (
              <div>
                <label
                  htmlFor="code"
                  className="block text-sm font-medium text-gray-700"
                >
                  验证码
                </label>
                <div className="mt-1 flex space-x-2">
                  <input
                    ref={codeRef}
                    id="code"
                    type="text"
                    autoComplete="off"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                    placeholder="请输入验证码"
                    maxLength={6}
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={handleSendCode}
                    disabled={sendingCode || countdown > 0}
                    className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap transition-colors"
                  >
                    {countdown > 0 ? `${countdown}s` : '获取验证码'}
                  </button>
                </div>
              </div>
            )}

            {/* 登录按钮 */}
            <button
              ref={submitRef}
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
            >
              {loading ? '登录中...' : '登录'}
            </button>
          </form>

          {/* 分隔线 */}
          <div className="mt-6">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-gray-300" />
              </div>
              <div className="relative flex justify-center text-sm">
                <span className="px-2 bg-white text-gray-500">或</span>
              </div>
            </div>
          </div>

          {/* 微信登录（预留） */}
          <div className="mt-6">
            <button
              type="button"
              disabled
              tabIndex={-1}
              className="w-full py-2.5 px-4 border border-gray-300 rounded-lg text-gray-400 bg-gray-50 cursor-not-allowed flex items-center justify-center space-x-2"
              title="微信登录功能即将上线"
            >
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 01.213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 00.167-.054l1.903-1.114a.864.864 0 01.717-.098 10.16 10.16 0 002.837.403c.276 0 .543-.027.811-.05-.857-2.578.157-4.972 1.932-6.446 1.703-1.415 3.882-1.98 5.853-1.838-.576-3.583-4.196-6.348-8.596-6.348zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178A1.17 1.17 0 014.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178 1.17 1.17 0 01-1.162-1.178c0-.651.52-1.18 1.162-1.18zm5.34 2.867c-1.797-.052-3.746.512-5.28 1.786-1.72 1.428-2.687 3.72-1.78 6.22.942 2.453 3.666 4.229 6.884 4.229.826 0 1.622-.12 2.361-.336a.722.722 0 01.598.082l1.584.926a.272.272 0 00.14.045c.134 0 .24-.111.24-.247 0-.06-.023-.12-.038-.177l-.327-1.233a.582.582 0 01-.023-.156.49.49 0 01.201-.398C23.024 18.48 24 16.82 24 14.98c0-3.21-2.931-5.837-6.656-6.088V8.89c-.135-.01-.269-.03-.407-.03zm-2.53 3.274c.535 0 .969.44.969.982a.976.976 0 01-.969.983.976.976 0 01-.969-.983c0-.542.434-.982.97-.982zm4.844 0c.535 0 .969.44.969.982a.976.976 0 01-.969.983.976.976 0 01-.969-.983c0-.542.434-.982.969-.982z" />
              </svg>
              <span>微信快捷登录（即将上线）</span>
            </button>
          </div>

          {/* 注册链接 */}
          <p className="mt-6 text-center text-sm text-gray-600">
            没有账号？
            <Link
              to="/register"
              tabIndex={-1}
              className="font-medium text-blue-600 hover:text-blue-500 ml-1"
            >
              立即注册
            </Link>
          </p>
        </div>
      </div>

      {/* 备案信息 */}
      <Footer className="mt-8" />
    </div>
  );
}
