/**
 * 企微 OAuth 回调着陆页
 *
 * 从 URL 读取一次性交接码，通过 POST 原子消费登录结果。
 */

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { exchangeWecomHandoff } from '../services/auth';

const ERROR_MESSAGES: Record<string, string> = {
  state_invalid: '二维码已过期，请重新扫码',
  not_member: '仅限企业成员使用扫码登录',
  api_error: '登录失败，请重试',
  user_disabled: '账号已被禁用，请联系管理员',
  already_bound: '该企微账号已绑定其他用户',
};

export default function WecomCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { setTokens, setUser, setCurrentOrg } = useAuthStore();
  const [error, setError] = useState('');

  useEffect(() => {
    const handoff = searchParams.get('handoff');
    const errorCode = searchParams.get('error');
    const errorMessage = searchParams.get('message');

    // 错误情况
    if (errorCode) {
      setError(ERROR_MESSAGES[errorCode] || errorMessage || '登录失败，请重试');
      return;
    }

    // 成功情况
    if (handoff) {
      const controller = new AbortController();
      void exchangeWecomHandoff(handoff, controller.signal)
        .then(({ token, user, org }) => {
          if (controller.signal.aborted) return;
          setTokens(token.access_token, token.refresh_token);
          setUser(user);
          if (org) {
            setCurrentOrg(org);
            localStorage.setItem('login_org_id', org.org_id);
          }
          const loginOrgId = org?.org_id || localStorage.getItem('login_org_id');
          navigate(loginOrgId ? `/?org=${loginOrgId}` : '/', { replace: true });
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setError('登录交接码已失效，请重新扫码');
          }
        });
      return () => controller.abort();
    }

    setError('无效的回调参数');
  }, [searchParams, setTokens, setUser, setCurrentOrg, navigate]);

  if (!error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface">
        <div className="text-center">
          <svg className="animate-spin h-10 w-10 text-accent mx-auto mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-text-tertiary">登录成功，正在跳转...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface">
      <div className="max-w-sm w-full bg-surface-card rounded-xl shadow-sm p-8 text-center">
        <div className="w-12 h-12 bg-error-light rounded-full flex items-center justify-center mx-auto mb-4">
          <svg className="h-6 w-6 text-error" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
        </div>
        <h2 className="text-lg font-medium text-text-primary mb-2">登录失败</h2>
        <p className="text-sm text-text-tertiary mb-6">{error}</p>
        <button
          onClick={() => {
            const loginOrgId = localStorage.getItem('login_org_id');
            navigate(loginOrgId ? `/?org=${loginOrgId}` : '/', { replace: true });
          }}
          className="w-full py-2.5 px-4 bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover transition-base font-medium"
        >
          返回登录
        </button>
      </div>
    </div>
  );
}
