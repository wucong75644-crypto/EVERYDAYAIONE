/** 判断 JWT 是否已过期或进入提前刷新窗口。 */
export function isAccessTokenExpired(
  token: string,
  nowMs: number = Date.now(),
  skewMs: number = 30000,
): boolean {
  const payloadPart = token.split('.')[1];
  if (!payloadPart) return false;

  try {
    const normalized = payloadPart.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    return (
      typeof payload.exp === 'number' &&
      payload.exp * 1000 <= nowMs + skewMs
    );
  } catch {
    // 非 JWT 或 payload 损坏时交给服务端认证。
    return false;
  }
}
