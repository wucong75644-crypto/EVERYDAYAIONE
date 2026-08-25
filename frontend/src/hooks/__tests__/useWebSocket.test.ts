import { describe, expect, it } from 'vitest';
import { isAccessTokenExpired } from '../useWebSocket';

function tokenWithExp(exp: number): string {
  const payload = btoa(JSON.stringify({ exp }));
  return `header.${payload}.signature`;
}

describe('isAccessTokenExpired', () => {
  it('识别已经过期的 JWT', () => {
    expect(isAccessTokenExpired(tokenWithExp(1_000), 2_000_000)).toBe(true);
  });

  it('在过期前的安全窗口内提前刷新', () => {
    expect(isAccessTokenExpired(tokenWithExp(2_030), 2_000_000)).toBe(true);
  });

  it('不过期的 JWT 不触发刷新', () => {
    expect(isAccessTokenExpired(tokenWithExp(2_100), 2_000_000)).toBe(false);
  });

  it('无法解析的 token 交给服务端处理', () => {
    expect(isAccessTokenExpired('not-a-jwt', 2_000_000)).toBe(false);
  });
});
