/**
 * 路由 key 归一化工具
 *
 * V3 Phase 12 引入：把路由 pathname 归一为"路由段" key，
 * 让 App.tsx 的 <AnimatePresence mode="wait"> 在带参路由内切换时
 * 不触发整页 unmount（/chat/abc → /chat/xyz 归一为同一个 'chat' key）。
 *
 * @example
 * ```ts
 * getRouteKey('/chat')         // '/chat'
 * getRouteKey('/chat/abc')     // '/chat'（归一化核心场景）
 * getRouteKey('/auth/wecom/callback')  // '/auth'
 * getRouteKey('/')             // '/'
 * getRouteKey('')              // '/'（兜底）
 * ```
 */
export function getRouteKey(pathname: string): string {
  // 取第一段作为 key：'/' / '/chat' / '/forgot-password' / '/auth' 等
  // /chat 和 /chat/xxx 都是 'chat'，不会触发 unmount
  const seg = pathname.split('/').filter(Boolean)[0] || '';
  return '/' + seg;
}
