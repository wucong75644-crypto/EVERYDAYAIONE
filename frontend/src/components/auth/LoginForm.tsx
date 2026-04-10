/**
 * 登录表单组件
 *
 * 支持两种登录方式：
 * 1. 手机号 + 密码（默认）
 * 2. 手机号 + 验证码（Tab 切换）
 */

import { useState, useEffect, useRef } from 'react';
import { useCountdown } from '../../hooks/useCountdown';
import { useAuthStore } from '../../stores/useAuthStore';
import { sendCode, loginByPhone, loginByPassword, loginByOrg, getOrgNamePublic, listMyOrganizations } from '../../services/auth';
import type { ApiErrorResponse } from '../../types/auth';
import { AxiosError } from 'axios';
import WecomQrLogin from './WecomQrLogin';

type LoginMode = 'password' | 'code' | 'enterprise' | 'wecom';

interface LoginFormProps {
  /** 登录成功后的回调 */
  onSuccess?: () => void;
  /** 切换到注册的回调 */
  onSwitchToRegister?: () => void;
  /** 企业 ID（URL ?org=xxx 传入，有值时自动显示企微扫码） */
  orgId?: string;
}

export default function LoginForm({
  onSuccess,
  onSwitchToRegister,
  orgId,
}: LoginFormProps) {
  const { setUser, setToken, setCurrentOrg } = useAuthStore();

  const [loginMode, setLoginMode] = useState<LoginMode>(orgId ? 'password' : 'password');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [orgName, setOrgName] = useState('');
  const [orgDisplayName, setOrgDisplayName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [error, setError] = useState('');
  const { countdown, startCountdown } = useCountdown(60);

  // 焦点循环 refs
  const phoneRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);

  // 企业专属链接：获取企业名称
  useEffect(() => {
    if (!orgId) return;
    getOrgNamePublic(orgId)
      .then((res) => setOrgDisplayName(res.name))
      .catch(() => setOrgDisplayName(''));
  }, [orgId]);

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
      startCountdown();
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

    if ((loginMode === 'password' || loginMode === 'enterprise') && !password) {
      setError('请输入密码');
      return;
    }

    if (loginMode === 'code' && !code) {
      setError('请输入验证码');
      return;
    }

    if (loginMode === 'enterprise' && !orgName.trim()) {
      setError('请输入企业名称');
      return;
    }

    setLoading(true);
    setError('');

    try {
      if (loginMode === 'enterprise') {
        const response = await loginByOrg({
          org_name: orgName.trim(),
          phone,
          password,
        });
        setToken(response.token.access_token);
        setUser(response.user);
        setCurrentOrg({
          org_id: response.org.org_id,
          name: response.org.org_name,
          role: response.org.org_role as 'owner' | 'admin' | 'member',
        });
      } else {
        const response =
          loginMode === 'password'
            ? await loginByPassword({ phone, password })
            : await loginByPhone({ phone, code });
        setToken(response.token.access_token);
        setUser(response.user);

        // 企业专属链接：登录后自动切入企业
        if (orgId) {
          try {
            const orgs = await listMyOrganizations();
            const targetOrg = orgs.find((o) => o.org_id === orgId);
            if (targetOrg) {
              setCurrentOrg(targetOrg);
            }
          } catch { /* 查不到企业不影响登录 */ }
        } else {
          // 普通登录 — 清除可能残留的企业上下文
          setCurrentOrg(null);
        }
      }

      // 记住手机号 + 企业来源（退出时跳回企业登录页）
      localStorage.setItem('last_login_phone', phone);
      if (orgId) {
        localStorage.setItem('login_org_id', orgId);
      } else {
        localStorage.removeItem('login_org_id');
      }

      // 触发成功回调
      onSuccess?.();
    } catch (err) {
      const error = err as AxiosError<ApiErrorResponse>;
      setError(error.response?.data?.error?.message || '登录失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  // 企微扫码模式：显示二维码组件
  if (loginMode === 'wecom') {
    return (
      <WecomQrLogin orgId={orgId} onBack={() => { setLoginMode('password'); setError(''); }} />
    );
  }

  return (
    <div>
      {/* 企业专属链接：显示企业名称 */}
      {orgId && orgDisplayName && (
        <div className="text-center mb-4">
          <span className="inline-block px-3 py-1 bg-accent-light text-accent text-sm rounded-full font-medium">
            {orgDisplayName}
          </span>
        </div>
      )}

      {/* 登录方式切换 */}
      <div className="flex border-b border-border-default mb-5">
        {(orgId
          ? (['password', 'code'] as const)
          : (['password', 'code', 'enterprise'] as const)
        ).map((mode) => (
          <button
            key={mode}
            type="button"
            tabIndex={-1}
            className={`flex-1 py-2 text-center font-medium transition-base text-sm ${
              loginMode === mode
                ? 'text-accent border-b-2 border-accent'
                : 'text-text-tertiary hover:text-text-secondary'
            }`}
            onClick={() => {
              setLoginMode(mode);
              setError('');
            }}
          >
            {mode === 'password' ? '账号登录' : mode === 'code' ? '验证码登录' : '企业登录'}
          </button>
        ))}
      </div>

      {/* 表单 */}
      <form onSubmit={handleSubmit} onKeyDown={handleKeyDown} className="space-y-3.5">
        {error && (
          <div className="bg-error-light text-error p-2.5 rounded-lg text-sm">
            {error}
          </div>
        )}

        {/* 企业名称（企业登录模式） */}
        {loginMode === 'enterprise' && (
          <div>
            <label htmlFor="orgName" className="block text-sm font-medium text-text-secondary">
              企业名称
            </label>
            <input
              id="orgName"
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className="mt-1 block w-full px-3 py-2 border border-border-default rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring"
              placeholder="请输入企业全称"
            />
          </div>
        )}

        {/* 手机号 */}
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

        {/* 密码登录模式 / 企业登录模式 */}
        {(loginMode === 'password' || loginMode === 'enterprise') && (
          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-text-secondary"
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
                className="block w-full px-3 py-2 border border-border-default rounded-lg shadow-sm focus:outline-none focus:ring-focus-ring focus:border-focus-ring pr-10"
                placeholder="请输入密码"
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
        )}

        {/* 验证码登录模式 */}
        {loginMode === 'code' && (
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
        )}

        {/* 登录按钮 */}
        <button
          ref={submitRef}
          type="submit"
          disabled={loading}
          className="w-full py-2.5 px-4 bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-focus-ring disabled:opacity-50 disabled:cursor-not-allowed transition-base font-medium"
        >
          {loading ? '登录中...' : '登录'}
        </button>
      </form>

      {/* 分隔线 + 企微扫码 — 仅企业专属链接时显示 */}
      {orgId && (
        <>
        <div className="mt-4">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-border-default" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="px-2 bg-surface-card text-text-tertiary">或</span>
            </div>
          </div>
        </div>
        </>
      )}
      {orgId && (
        <div className="mt-4">
          <button
            type="button"
            tabIndex={-1}
            onClick={() => {
              setLoginMode('wecom');
              setError('');
            }}
            className="w-full py-2.5 px-4 border border-border-default rounded-lg text-text-secondary bg-surface-card hover:bg-surface flex items-center justify-center space-x-2 transition-base"
          >
            <svg className="h-5 w-5 text-accent" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 01.213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 00.167-.054l1.903-1.114a.864.864 0 01.717-.098 10.16 10.16 0 002.837.403c.276 0 .543-.027.811-.05-.857-2.578.157-4.972 1.932-6.446 1.703-1.415 3.882-1.98 5.853-1.838-.576-3.583-4.196-6.348-8.596-6.348zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178A1.17 1.17 0 014.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178 1.17 1.17 0 01-1.162-1.178c0-.651.52-1.18 1.162-1.18zm5.34 2.867c-1.797-.052-3.746.512-5.28 1.786-1.72 1.428-2.687 3.72-1.78 6.22.942 2.453 3.666 4.229 6.884 4.229.826 0 1.622-.12 2.361-.336a.722.722 0 01.598.082l1.584.926a.272.272 0 00.14.045c.134 0 .24-.111.24-.247 0-.06-.023-.12-.038-.177l-.327-1.233a.582.582 0 01-.023-.156.49.49 0 01.201-.398C23.024 18.48 24 16.82 24 14.98c0-3.21-2.931-5.837-6.656-6.088V8.89c-.135-.01-.269-.03-.407-.03zm-2.53 3.274c.535 0 .969.44.969.982a.976.976 0 01-.969.983.976.976 0 01-.969-.983c0-.542.434-.982.97-.982zm4.844 0c.535 0 .969.44.969.982a.976.976 0 01-.969.983.976.976 0 01-.969-.983c0-.542.434-.982.969-.982z" />
            </svg>
            <span>企业微信扫码登录</span>
          </button>
        </div>
      )}

      {/* 注册链接 — 企业专属链接不显示注册入口 */}
      {onSwitchToRegister && !orgId && (
        <p className="mt-4 text-center text-sm text-text-tertiary">
          没有账号？
          <button
            type="button"
            onClick={onSwitchToRegister}
            className="font-medium text-accent hover:text-accent ml-1"
          >
            立即注册
          </button>
        </p>
      )}
    </div>
  );
}
