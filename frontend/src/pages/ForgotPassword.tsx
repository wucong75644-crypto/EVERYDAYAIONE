/**
 * 忘记密码页面
 *
 * 两步流程：
 * 1. 输入手机号，获取验证码
 * 2. 验证成功后设置新密码
 */

import { useState, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { sendCode } from '../services/auth';
import { request } from '../services/api';
import type { ApiErrorResponse } from '../types/auth';
import { AxiosError } from 'axios';
import { useCountdown } from '../hooks/useCountdown';
import Footer from '../components/Footer';

type Step = 'verify' | 'reset';

export default function ForgotPassword() {
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>('verify');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const { countdown, startCountdown } = useCountdown(60);

  // 步骤1 焦点循环 refs
  const phoneRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const verifySubmitRef = useRef<HTMLButtonElement>(null);

  // 步骤2 焦点循环 refs
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmPasswordRef = useRef<HTMLInputElement>(null);
  const resetSubmitRef = useRef<HTMLButtonElement>(null);

  // 步骤1 Tab 键焦点循环处理
  const handleVerifyKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab') return;

    const activeElement = document.activeElement;

    if (e.shiftKey && activeElement === phoneRef.current) {
      e.preventDefault();
      verifySubmitRef.current?.focus();
    } else if (!e.shiftKey && activeElement === verifySubmitRef.current) {
      e.preventDefault();
      phoneRef.current?.focus();
    }
  };

  // 步骤2 Tab 键焦点循环处理
  const handleResetKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab') return;

    const activeElement = document.activeElement;

    if (e.shiftKey && activeElement === passwordRef.current) {
      e.preventDefault();
      resetSubmitRef.current?.focus();
    } else if (!e.shiftKey && activeElement === resetSubmitRef.current) {
      e.preventDefault();
      passwordRef.current?.focus();
    }
  };

  const validatePhone = (phone: string): boolean => {
    return /^1[3-9]\d{9}$/.test(phone);
  };

  const validatePassword = (password: string): boolean => {
    return password.length >= 8 && /[a-zA-Z]/.test(password) && /\d/.test(password);
  };

  const handleSendCode = async () => {
    if (!validatePhone(phone)) {
      setError('请输入正确的手机号');
      return;
    }

    setSendingCode(true);
    setError('');

    try {
      await sendCode({ phone, purpose: 'reset_password' });
      startCountdown();
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '发送验证码失败');
    } finally {
      setSendingCode(false);
    }
  };

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validatePhone(phone)) {
      setError('请输入正确的手机号');
      return;
    }

    if (!code || code.length !== 6) {
      setError('请输入6位验证码');
      return;
    }

    setLoading(true);
    setError('');

    try {
      // 验证验证码
      await request({
        method: 'POST',
        url: '/auth/verify-code',
        data: { phone, code, purpose: 'reset_password' },
      });

      // 验证成功，进入设置新密码步骤
      setStep('reset');
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '验证码错误');
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validatePassword(password)) {
      setError('密码至少8位，需包含字母和数字');
      return;
    }

    if (password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }

    setLoading(true);
    setError('');

    try {
      await request({
        method: 'POST',
        url: '/auth/reset-password',
        data: { phone, code, new_password: password },
      });

      setSuccess(true);
      // 3秒后跳转到首页（项目使用弹窗登录，无 /login 页面）
      setTimeout(() => {
        navigate('/');
      }, 3000);
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '重置密码失败');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="min-h-screen bg-surface flex flex-col justify-center py-12 sm:px-6 lg:px-8">
        <div className="sm:mx-auto sm:w-full sm:max-w-md">
          <div className="bg-surface-card py-8 px-4 shadow-lg sm:rounded-lg sm:px-10 text-center">
            <div className="mx-auto flex items-center justify-center h-12 w-12 rounded-full bg-success-light mb-4">
              <svg className="h-6 w-6 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-text-primary mb-2">密码重置成功</h3>
            <p className="text-sm text-text-tertiary mb-4">即将跳转到首页...</p>
            <Link
              to="/"
              className="text-accent hover:text-accent font-medium"
            >
              返回首页
            </Link>
          </div>
        </div>

        {/* 备案信息 */}
        <Footer className="mt-8" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-surface flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <Link to="/" tabIndex={-1} className="flex justify-center">
          <span className="text-3xl font-bold text-accent">每日AI</span>
        </Link>
        <h2 className="mt-6 text-center text-2xl font-bold text-text-primary">
          {step === 'verify' ? '忘记密码' : '设置新密码'}
        </h2>
        {step === 'verify' && (
          <p className="mt-2 text-center text-sm text-text-tertiary">
            请输入注册时使用的手机号
          </p>
        )}
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-surface-card py-8 px-4 shadow-lg sm:rounded-lg sm:px-10">
          {/* 步骤指示器 */}
          <div className="flex items-center justify-center mb-6">
            <div className="flex items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  step === 'verify'
                    ? 'bg-accent text-text-on-accent'
                    : 'bg-success text-white'
                }`}
              >
                {step === 'verify' ? '1' : '✓'}
              </div>
              <span className="ml-2 text-sm text-text-tertiary">验证手机</span>
            </div>
            <div className="w-12 h-0.5 bg-active mx-3" />
            <div className="flex items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  step === 'reset'
                    ? 'bg-accent text-text-on-accent'
                    : 'bg-active text-text-tertiary'
                }`}
              >
                2
              </div>
              <span className="ml-2 text-sm text-text-tertiary">设置密码</span>
            </div>
          </div>

          {error && (
            <div className="bg-error-light text-error p-3 rounded-lg text-sm mb-5">
              {error}
            </div>
          )}

          {/* 步骤1：验证手机号 */}
          {step === 'verify' && (
            <form onSubmit={handleVerify} onKeyDown={handleVerifyKeyDown} className="space-y-5">
              <div>
                <label
                  htmlFor="phone"
                  className="block text-sm font-medium text-text-secondary"
                >
                  手机号
                </label>
                <input
                  ref={phoneRef}
                  id="phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  className="mt-1 block w-full px-3 py-2 border border-border-default rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring"
                  placeholder="请输入手机号"
                  maxLength={11}
                />
              </div>

              <div>
                <label
                  htmlFor="code"
                  className="block text-sm font-medium text-text-secondary"
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
                    className="flex-1 px-3 py-2 border border-border-default rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring"
                    placeholder="请输入验证码"
                    maxLength={6}
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={handleSendCode}
                    disabled={sendingCode || countdown > 0}
                    className="px-4 py-2 bg-hover text-text-secondary rounded-lg hover:bg-active disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap transition-base"
                  >
                    {countdown > 0 ? `${countdown}s` : '获取验证码'}
                  </button>
                </div>
              </div>

              <button
                ref={verifySubmitRef}
                type="submit"
                disabled={loading}
                className="w-full py-2.5 px-4 bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-focus-ring disabled:opacity-50 disabled:cursor-not-allowed transition-base font-medium"
              >
                {loading ? '验证中...' : '下一步'}
              </button>
            </form>
          )}

          {/* 步骤2：设置新密码 */}
          {step === 'reset' && (
            <form onSubmit={handleResetPassword} onKeyDown={handleResetKeyDown} className="space-y-5">
              <div>
                <label
                  htmlFor="password"
                  className="block text-sm font-medium text-text-secondary"
                >
                  新密码
                </label>
                <div className="mt-1 relative">
                  <input
                    ref={passwordRef}
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="block w-full px-3 py-2 border border-border-default rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring pr-10"
                    placeholder="至少8位，包含字母和数字"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    className="absolute inset-y-0 right-0 pr-3 flex items-center text-text-disabled hover:text-text-tertiary"
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
              </div>

              <div>
                <label
                  htmlFor="confirmPassword"
                  className="block text-sm font-medium text-text-secondary"
                >
                  确认新密码
                </label>
                <input
                  ref={confirmPasswordRef}
                  id="confirmPassword"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className={`mt-1 block w-full px-3 py-2 border rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring ${
                    confirmPassword && confirmPassword !== password
                      ? 'border-error/30'
                      : 'border-border-default'
                  }`}
                  placeholder="请再次输入新密码"
                />
                {confirmPassword && confirmPassword !== password && (
                  <p className="mt-1 text-xs text-error">两次输入的密码不一致</p>
                )}
              </div>

              <button
                ref={resetSubmitRef}
                type="submit"
                disabled={loading}
                className="w-full py-2.5 px-4 bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-focus-ring disabled:opacity-50 disabled:cursor-not-allowed transition-base font-medium"
              >
                {loading ? '提交中...' : '重置密码'}
              </button>
            </form>
          )}

          {/* 返回首页 */}
          <p className="mt-6 text-center text-sm text-text-tertiary">
            <Link
              to="/"
              tabIndex={-1}
              className="font-medium text-accent hover:text-accent"
            >
              ← 返回首页
            </Link>
          </p>
        </div>
      </div>

      {/* 备案信息 */}
      <Footer className="mt-8" />
    </div>
  );
}
