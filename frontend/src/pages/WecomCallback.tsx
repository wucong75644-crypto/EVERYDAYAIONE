/**
 * 企微 OAuth 回调着陆页
 *
 * 从 URL 解析 token + user（base64 编码）或 error，
 * 成功时存储认证信息并跳转到 /chat，失败时显示错误提示。
 */

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import type { User, TokenInfo } from '../types/auth';

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
  const { setToken, setUser } = useAuthStore();
  const [error, setError] = useState('');

  useEffect(() => {
    const tokenB64 = searchParams.get('token');
    const userB64 = searchParams.get('user');
    const errorCode = searchParams.get('error');
    const errorMessage = searchParams.get('message');

    // 错误情况
    if (errorCode) {
      setError(ERROR_MESSAGES[errorCode] || errorMessage || '登录失败，请重试');
      return;
    }

    // 成功情况
    if (tokenB64 && userB64) {
      try {
        const tokenData: TokenInfo = JSON.parse(atob(tokenB64));
        const userData: User = JSON.parse(atob(userB64));

        setToken(tokenData.access_token);
        setUser(userData);

        navigate('/chat', { replace: true });
      } catch {
        setError('登录数据解析失败，请重试');
      }
      return;
    }

    setError('无效的回调参数');
  }, [searchParams, setToken, setUser, navigate]);

  if (!error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <svg className="animate-spin h-10 w-10 text-blue-600 mx-auto mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-gray-600">登录成功，正在跳转...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-sm w-full bg-white rounded-xl shadow-sm p-8 text-center">
        <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <svg className="h-6 w-6 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
        </div>
        <h2 className="text-lg font-medium text-gray-900 mb-2">登录失败</h2>
        <p className="text-sm text-gray-500 mb-6">{error}</p>
        <button
          onClick={() => navigate('/', { replace: true })}
          className="w-full py-2.5 px-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
        >
          返回登录
        </button>
      </div>
    </div>
  );
}
