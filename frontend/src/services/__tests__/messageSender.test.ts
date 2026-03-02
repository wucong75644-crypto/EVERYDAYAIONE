/**
 * messageSender 工具函数单元测试
 *
 * 测试辅助函数的正确性，主函数 sendMessage 需要集成测试
 */

import { describe, it, expect } from 'vitest';
import {
  createTextContent,
  createTextWithImages,
  getTextFromContent,
  inferGenerationType,
  determineMessageType,
  extractModelId,
  extractGenerationParams,
} from '../messageSender';
import type { Message, ContentPart } from '../../stores/useMessageStore';

// ============================================================
// 辅助函数
// ============================================================

function createTestMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: [{ type: 'text', text: 'test' }],
    status: 'completed',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ============================================================
// createTextContent 测试
// ============================================================

describe('createTextContent', () => {
  it('should create text content array', () => {
    const result = createTextContent('Hello World');

    expect(result).toEqual([{ type: 'text', text: 'Hello World' }]);
  });

  it('should handle empty string', () => {
    const result = createTextContent('');

    expect(result).toEqual([{ type: 'text', text: '' }]);
  });
});

// ============================================================
// createTextWithImages 测试
// ============================================================

describe('createTextWithImages', () => {
  it('should create text and single image content array', () => {
    const result = createTextWithImages('描述图片', ['https://example.com/image.jpg']);

    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ type: 'text', text: '描述图片' });
    expect(result[1]).toEqual({ type: 'image', url: 'https://example.com/image.jpg' });
  });

  it('should create text and multiple images content array', () => {
    const result = createTextWithImages('编辑这些图片', [
      'https://example.com/a.jpg',
      'https://example.com/b.png',
      'https://example.com/c.webp',
    ]);

    expect(result).toHaveLength(4);
    expect(result[0]).toEqual({ type: 'text', text: '编辑这些图片' });
    expect(result[1]).toEqual({ type: 'image', url: 'https://example.com/a.jpg' });
    expect(result[2]).toEqual({ type: 'image', url: 'https://example.com/b.png' });
    expect(result[3]).toEqual({ type: 'image', url: 'https://example.com/c.webp' });
  });

  it('should preserve image order', () => {
    const urls = ['https://example.com/1.jpg', 'https://example.com/2.jpg'];
    const result = createTextWithImages('test', urls);

    expect(result[1]).toEqual({ type: 'image', url: 'https://example.com/1.jpg' });
    expect(result[2]).toEqual({ type: 'image', url: 'https://example.com/2.jpg' });
  });

  it('should handle empty image array', () => {
    const result = createTextWithImages('纯文本', []);

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({ type: 'text', text: '纯文本' });
  });
});

// ============================================================
// getTextFromContent 测试
// ============================================================

describe('getTextFromContent', () => {
  it('should extract text from content array', () => {
    const content: ContentPart[] = [{ type: 'text', text: 'Hello World' }];

    const result = getTextFromContent(content);

    expect(result).toBe('Hello World');
  });

  it('should return first text part when multiple exist', () => {
    const content: ContentPart[] = [
      { type: 'image', url: 'https://example.com/image.jpg' },
      { type: 'text', text: 'First' },
      { type: 'text', text: 'Second' },
    ];

    const result = getTextFromContent(content);

    expect(result).toBe('First');
  });

  it('should return empty string when no text part exists', () => {
    const content: ContentPart[] = [
      { type: 'image', url: 'https://example.com/image.jpg' },
    ];

    const result = getTextFromContent(content);

    expect(result).toBe('');
  });

  it('should return empty string for empty array', () => {
    const result = getTextFromContent([]);

    expect(result).toBe('');
  });
});

// ============================================================
// inferGenerationType 测试
// ============================================================

describe('inferGenerationType', () => {
  it('should return "image" for image generation keywords (Chinese)', () => {
    expect(inferGenerationType([{ type: 'text', text: '生成图片：一只猫' }])).toBe('image');
    expect(inferGenerationType([{ type: 'text', text: '画一幅风景画' }])).toBe('image');
  });

  it('should return "image" for image generation keywords (English)', () => {
    expect(inferGenerationType([{ type: 'text', text: 'generate image of a cat' }])).toBe('image');
    expect(inferGenerationType([{ type: 'text', text: '/image a beautiful sunset' }])).toBe('image');
  });

  it('should return "video" for video generation keywords (Chinese)', () => {
    expect(inferGenerationType([{ type: 'text', text: '生成视频：跳舞的人' }])).toBe('video');
    expect(inferGenerationType([{ type: 'text', text: '做个视频展示产品' }])).toBe('video');
  });

  it('should return "video" for video generation keywords (English)', () => {
    expect(inferGenerationType([{ type: 'text', text: 'generate video of ocean waves' }])).toBe('video');
    expect(inferGenerationType([{ type: 'text', text: '/video dancing animation' }])).toBe('video');
  });

  it('should return "chat" for regular messages', () => {
    expect(inferGenerationType([{ type: 'text', text: '你好，请问今天天气怎么样？' }])).toBe('chat');
    expect(inferGenerationType([{ type: 'text', text: 'Hello, how are you?' }])).toBe('chat');
  });

  it('should return "chat" for empty content', () => {
    expect(inferGenerationType([])).toBe('chat');
  });

  it('should be case insensitive', () => {
    expect(inferGenerationType([{ type: 'text', text: 'GENERATE IMAGE' }])).toBe('image');
    expect(inferGenerationType([{ type: 'text', text: 'Generate Video' }])).toBe('video');
  });
});

// ============================================================
// determineMessageType 测试
// ============================================================

describe('determineMessageType', () => {
  it('should return type from generation_params if available', () => {
    const message = createTestMessage({
      generation_params: { type: 'image' },
    });

    expect(determineMessageType(message)).toBe('image');
  });

  it('should return "video" if content contains video', () => {
    const message = createTestMessage({
      content: [
        { type: 'text', text: 'Generated video' },
        { type: 'video', url: 'https://example.com/video.mp4' },
      ],
    });

    expect(determineMessageType(message)).toBe('video');
  });

  it('should return "image" if content contains image', () => {
    const message = createTestMessage({
      content: [
        { type: 'text', text: 'Generated image' },
        { type: 'image', url: 'https://example.com/image.jpg' },
      ],
    });

    expect(determineMessageType(message)).toBe('image');
  });

  it('should return "chat" for text-only content', () => {
    const message = createTestMessage({
      content: [{ type: 'text', text: 'Hello' }],
    });

    expect(determineMessageType(message)).toBe('chat');
  });

  it('should prioritize generation_params over content', () => {
    const message = createTestMessage({
      generation_params: { type: 'chat' },
      content: [
        { type: 'image', url: 'https://example.com/image.jpg' },
      ],
    });

    expect(determineMessageType(message)).toBe('chat');
  });
});

// ============================================================
// extractModelId 测试
// ============================================================

describe('extractModelId', () => {
  it('should extract model from generation_params', () => {
    const message = createTestMessage({
      generation_params: { model: 'gemini-3-pro' },
    });

    expect(extractModelId(message)).toBe('gemini-3-pro');
  });

  it('should return undefined if no model in generation_params', () => {
    const message = createTestMessage({
      generation_params: { type: 'chat' },
    });

    expect(extractModelId(message)).toBeUndefined();
  });

  it('should return undefined if no generation_params', () => {
    const message = createTestMessage();

    expect(extractModelId(message)).toBeUndefined();
  });
});

// ============================================================
// extractGenerationParams 测试
// ============================================================

describe('extractGenerationParams', () => {
  it('should extract chat params', () => {
    const message = createTestMessage({
      generation_params: {
        thinking_effort: 'high',
        thinking_mode: 'deep_think',
      },
    });

    const result = extractGenerationParams(message);

    expect(result).toEqual({
      thinking_effort: 'high',
      thinking_mode: 'deep_think',
    });
  });

  it('should extract image params', () => {
    const message = createTestMessage({
      generation_params: {
        aspect_ratio: '16:9',
        resolution: '1024x1024',
        output_format: 'png',
      },
    });

    const result = extractGenerationParams(message);

    expect(result).toEqual({
      aspect_ratio: '16:9',
      resolution: '1024x1024',
      output_format: 'png',
    });
  });

  it('should extract video params', () => {
    const message = createTestMessage({
      generation_params: {
        n_frames: '50',
        remove_watermark: true,
      },
    });

    const result = extractGenerationParams(message);

    expect(result).toEqual({
      n_frames: '50',
      remove_watermark: true,
    });
  });

  it('should handle remove_watermark being false', () => {
    const message = createTestMessage({
      generation_params: {
        remove_watermark: false,
      },
    });

    const result = extractGenerationParams(message);

    expect(result).toEqual({
      remove_watermark: false,
    });
  });

  it('should return empty object if no generation_params', () => {
    const message = createTestMessage();

    const result = extractGenerationParams(message);

    expect(result).toEqual({});
  });

  it('should only include defined params', () => {
    const message = createTestMessage({
      generation_params: {
        thinking_effort: 'high',
        // thinking_mode is not defined
      },
    });

    const result = extractGenerationParams(message);

    expect(result).toEqual({
      thinking_effort: 'high',
    });
    expect(result).not.toHaveProperty('thinking_mode');
  });
});
