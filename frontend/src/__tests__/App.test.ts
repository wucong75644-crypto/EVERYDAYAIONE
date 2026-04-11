/**
 * App.tsx 路由层工具函数测试
 *
 * 当前只测 getRouteKey 纯函数（V3 Phase 12 引入）。
 * App 组件本身包含 BrowserRouter + WebSocketProvider + Suspense + lazy import
 * 的整合，完整渲染测试属于 E2E 范畴，不在单元测试范围。
 *
 * getRouteKey 的作用：把路由 pathname 归一为"路由段" key，让 AnimatePresence
 * 在 /chat/:id 这种带参路由内切换时不触发整页 unmount。
 *
 * 这是 V3 Review HIGH-1 fix 的核心逻辑，必须保护。
 */

import { describe, it, expect } from 'vitest';
import { getRouteKey } from '../App';

describe('getRouteKey', () => {
  it('根路径 "/" 返回 "/"', () => {
    expect(getRouteKey('/')).toBe('/');
  });

  it('空字符串返回 "/"（兜底）', () => {
    expect(getRouteKey('')).toBe('/');
  });

  it('/chat 返回 /chat', () => {
    expect(getRouteKey('/chat')).toBe('/chat');
  });

  it('/chat/abc 返回 /chat（带参路由归一化，核心场景）', () => {
    expect(getRouteKey('/chat/abc')).toBe('/chat');
  });

  it('/chat/abc-123-xyz 返回 /chat（UUID 风格 conversation id）', () => {
    expect(getRouteKey('/chat/abc-123-xyz')).toBe('/chat');
  });

  it('/chat/uuid/nested 返回 /chat（多层嵌套也只取首段）', () => {
    expect(getRouteKey('/chat/uuid/nested')).toBe('/chat');
  });

  it('/forgot-password 返回 /forgot-password（单层路由）', () => {
    expect(getRouteKey('/forgot-password')).toBe('/forgot-password');
  });

  it('/auth/wecom/callback 返回 /auth（企微 callback 正确归一化）', () => {
    expect(getRouteKey('/auth/wecom/callback')).toBe('/auth');
  });

  it('/unknown 返回 /unknown（未匹配路由也不崩溃）', () => {
    expect(getRouteKey('/unknown')).toBe('/unknown');
  });

  it('连续斜杠 //chat 仍归一化到 /chat（filter(Boolean) 去空串）', () => {
    expect(getRouteKey('//chat')).toBe('/chat');
  });

  it('trailing slash /chat/ 与 /chat 一致', () => {
    expect(getRouteKey('/chat/')).toBe('/chat');
  });
});
