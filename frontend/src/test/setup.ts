/**
 * Vitest 测试环境配置
 *
 * 包含：
 * - @testing-library/jest-dom 断言扩展
 * - 自动 cleanup / mock 清理
 * - matchMedia polyfill（jsdom 缺）
 * - framer-motion skipAnimations + IntersectionObserver/ResizeObserver polyfill
 *   （motion-mock.ts，V3 设计系统重构引入）
 */

import { expect, afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import * as matchers from '@testing-library/jest-dom/matchers';

// framer-motion：跳过所有动画 + IntersectionObserver/ResizeObserver polyfill
import './motion-mock';

// 扩展 Vitest 断言
expect.extend(matchers);

// 每个测试后清理
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
