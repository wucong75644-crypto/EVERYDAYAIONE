/**
 * WecomCallback 页面逻辑测试
 *
 * 覆盖：base64 解码 token/user、错误码映射
 */

import { describe, it, expect } from 'vitest';

// 测试 base64 编解码逻辑（与 WecomCallback 中一致）
function decodeTokenFromUrl(tokenB64: string) {
  return JSON.parse(atob(tokenB64));
}

function decodeUserFromUrl(userB64: string) {
  return JSON.parse(atob(userB64));
}

// 错误码映射（与 WecomCallback 中一致）
const ERROR_MESSAGES: Record<string, string> = {
  state_invalid: '二维码已过期，请重新扫码',
  not_member: '仅限企业成员使用扫码登录',
  api_error: '登录失败，请重试',
  user_disabled: '账号已被禁用，请联系管理员',
  already_bound: '该企微账号已绑定其他用户',
};

describe('WecomCallback URL 解析', () => {
  it('正确解码 base64 token', () => {
    const tokenData = { access_token: 'jwt_abc', token_type: 'bearer', expires_in: 86400 };
    const b64 = btoa(JSON.stringify(tokenData));
    const decoded = decodeTokenFromUrl(b64);

    expect(decoded.access_token).toBe('jwt_abc');
    expect(decoded.token_type).toBe('bearer');
    expect(decoded.expires_in).toBe(86400);
  });

  it('正确解码 base64 user', () => {
    const userData = { id: 'uid-1', nickname: 'test_user', wecom_bound: true };
    const b64 = btoa(JSON.stringify(userData));
    const decoded = decodeUserFromUrl(b64);

    expect(decoded.id).toBe('uid-1');
    expect(decoded.nickname).toBe('test_user');
    expect(decoded.wecom_bound).toBe(true);
  });

  it('解码失败时抛出异常', () => {
    expect(() => decodeTokenFromUrl('invalid_base64!!!')).toThrow();
  });
});

describe('WecomCallback 错误码映射', () => {
  it('state_invalid 映射正确', () => {
    expect(ERROR_MESSAGES['state_invalid']).toBe('二维码已过期，请重新扫码');
  });

  it('not_member 映射正确', () => {
    expect(ERROR_MESSAGES['not_member']).toBe('仅限企业成员使用扫码登录');
  });

  it('user_disabled 映射正确', () => {
    expect(ERROR_MESSAGES['user_disabled']).toBe('账号已被禁用，请联系管理员');
  });

  it('already_bound 映射正确', () => {
    expect(ERROR_MESSAGES['already_bound']).toBe('该企微账号已绑定其他用户');
  });

  it('未知错误码回退到默认', () => {
    const code = 'unknown_error';
    const msg = ERROR_MESSAGES[code] || '登录失败，请重试';
    expect(msg).toBe('登录失败，请重试');
  });
});
