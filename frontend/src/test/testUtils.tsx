/**
 * 测试工具函数
 */

import type { ReactElement } from 'react';
import { render, renderHook } from '@testing-library/react';
import type { RenderOptions, RenderHookOptions } from '@testing-library/react';
import { vi } from 'vitest';

/**
 * 自定义 render 函数（可添加全局 Provider）
 */
export function customRender(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
) {
  return render(ui, { ...options });
}

/**
 * 自定义 renderHook 函数
 */
export function customRenderHook<Result, Props>(
  render: (initialProps: Props) => Result,
  options?: RenderHookOptions<Props>
) {
  return renderHook(render, options);
}

/**
 * Mock Message 数据
 */
export const mockMessage = {
  id: 'msg-1',
  conversation_id: 'conv-1',
  role: 'user' as const,
  content: 'Test message',
  created_at: new Date().toISOString(),
  credits_cost: 0,
  is_error: false,
};

/**
 * Mock UnifiedModel 数据
 */
export const mockChatModel = {
  id: 'test-chat-model',
  name: 'Test Chat Model',
  type: 'chat' as const,
  description: 'Test chat model for unit tests',
  capabilities: {
    textToImage: false,
    imageEditing: false,
    imageToVideo: false,
    textToVideo: false,
    vqa: true,
    videoQA: false,
    audioInput: false,
    pdfInput: false,
    functionCalling: false,
    structuredOutput: false,
    thinkingEffort: false,
    streamingResponse: true,
  },
  credits: 1,
};

export const mockImageModel = {
  id: 'test-image-model',
  name: 'Test Image Model',
  type: 'image' as const,
  description: 'Test image model for unit tests',
  capabilities: {
    textToImage: true,
    imageEditing: true,
    imageToVideo: false,
    textToVideo: false,
    vqa: false,
    videoQA: false,
    audioInput: false,
    pdfInput: false,
    functionCalling: false,
    structuredOutput: false,
    thinkingEffort: false,
    streamingResponse: false,
  },
  credits: 5,
  supportsResolution: true,
};

export const mockVideoModel = {
  id: 'test-video-model',
  name: 'Test Video Model',
  type: 'video' as const,
  description: 'Test video model for unit tests',
  capabilities: {
    textToImage: false,
    imageEditing: false,
    imageToVideo: true,
    textToVideo: true,
    vqa: false,
    videoQA: false,
    audioInput: false,
    pdfInput: false,
    functionCalling: false,
    structuredOutput: false,
    thinkingEffort: false,
    streamingResponse: false,
  },
  credits: { '10': 30, '15': 45 },
  videoPricing: { '10': 30, '15': 45 },
};

/**
 * 延迟工具函数
 */
export const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Mock 异步函数
 */
export const mockAsyncFn = <T,>(value: T, delayMs = 0) => {
  return vi.fn().mockImplementation(() => delay(delayMs).then(() => value));
};

// Re-export testing library utilities
// eslint-disable-next-line react-refresh/only-export-components
export * from '@testing-library/react';
export { default as userEvent } from '@testing-library/user-event';
