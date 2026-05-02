/**
 * messageUtils 工具函数单元测试
 *
 * 覆盖：
 * 1. getImageUrls：过滤 null/undefined URL、正常 URL 保留、混合场景
 * 2. getTextContent：提取文本内容
 * 3. getVideoUrls：提取视频 URL
 * 4. normalizeMessage：旧格式转换
 */

import { describe, it, expect } from 'vitest';
import {
  getImageUrls,
  getTextContent,
  getVideoUrls,
  getFiles,
  normalizeMessage,
  calcRemainingText,
} from '../messageUtils';
import type { Message, ContentPart } from '../../types/message';

// ============================================================
// 辅助函数
// ============================================================

function createTestMessage(content: ContentPart[], overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content,
    status: 'completed',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ============================================================
// getImageUrls 测试
// ============================================================

describe('getImageUrls', () => {
  it('should return URLs from valid image parts', () => {
    const msg = createTestMessage([
      { type: 'image', url: 'https://oss/img0.png' },
      { type: 'image', url: 'https://oss/img1.png' },
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual(['https://oss/img0.png', 'https://oss/img1.png']);
  });

  it('should filter out image parts with null URL (multi-image pending slot)', () => {
    const msg = createTestMessage([
      { type: 'image', url: 'https://oss/img0.png' },
      { type: 'image', url: null } as unknown as ContentPart,
      { type: 'image', url: 'https://oss/img2.png' },
      { type: 'image', url: null } as unknown as ContentPart,
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual(['https://oss/img0.png', 'https://oss/img2.png']);
  });

  it('should filter out image parts with undefined URL', () => {
    const msg = createTestMessage([
      { type: 'image', url: undefined } as unknown as ContentPart,
      { type: 'image', url: 'https://oss/valid.png' },
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual(['https://oss/valid.png']);
  });

  it('should filter out image parts with empty string URL', () => {
    const msg = createTestMessage([
      { type: 'image', url: '' } as unknown as ContentPart,
      { type: 'image', url: 'https://oss/valid.png' },
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual(['https://oss/valid.png']);
  });

  it('should return empty array when all image URLs are null', () => {
    const msg = createTestMessage([
      { type: 'image', url: null } as unknown as ContentPart,
      { type: 'image', url: null } as unknown as ContentPart,
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual([]);
  });

  it('should skip non-image content parts', () => {
    const msg = createTestMessage([
      { type: 'text', text: 'Hello' },
      { type: 'image', url: 'https://oss/img.png' },
      { type: 'video', url: 'https://oss/video.mp4' },
    ]);

    const result = getImageUrls(msg);

    expect(result).toEqual(['https://oss/img.png']);
  });

  it('should return empty array for non-array content', () => {
    const msg = { content: 'old string format' } as unknown as Message;

    const result = getImageUrls(msg);

    expect(result).toEqual([]);
  });

  it('should return empty array for empty content', () => {
    const msg = createTestMessage([]);

    const result = getImageUrls(msg);

    expect(result).toEqual([]);
  });

  it('should handle multi-image batch with mixed null and valid (4-image grid)', () => {
    // 模拟 4 张图的批次，2 张完成 2 张 pending
    const msg = createTestMessage([
      { type: 'image', url: 'https://oss/img0.png', width: 1024, height: 1024 },
      { type: 'image', url: null } as unknown as ContentPart,
      { type: 'image', url: 'https://oss/img2.png', width: 1024, height: 1024 },
      { type: 'image', url: null } as unknown as ContentPart,
    ]);

    const result = getImageUrls(msg);

    expect(result).toHaveLength(2);
    expect(result).toEqual(['https://oss/img0.png', 'https://oss/img2.png']);
  });
});

// ============================================================
// getTextContent 测试
// ============================================================

describe('getTextContent', () => {
  it('should extract text from content array', () => {
    const msg = createTestMessage([{ type: 'text', text: 'Hello World' }]);

    expect(getTextContent(msg)).toBe('Hello World');
  });

  it('should join all text parts (multi-block mode)', () => {
    const msg = createTestMessage([
      { type: 'image', url: 'https://example.com/img.jpg' },
      { type: 'text', text: 'First' },
      { type: 'text', text: 'Second' },
    ]);

    expect(getTextContent(msg)).toBe('First\n\nSecond');
  });

  it('should return empty string for no text parts', () => {
    const msg = createTestMessage([
      { type: 'image', url: 'https://example.com/img.jpg' },
    ]);

    expect(getTextContent(msg)).toBe('');
  });

  it('should handle old string format', () => {
    const msg = { content: 'old format text' } as unknown as Message;

    expect(getTextContent(msg)).toBe('old format text');
  });
});

// ============================================================
// getVideoUrls 测试
// ============================================================

describe('getVideoUrls', () => {
  it('should extract video URLs', () => {
    const msg = createTestMessage([
      { type: 'video', url: 'https://oss/video.mp4' },
    ]);

    expect(getVideoUrls(msg)).toEqual(['https://oss/video.mp4']);
  });

  it('should skip non-video parts', () => {
    const msg = createTestMessage([
      { type: 'image', url: 'https://oss/img.png' },
      { type: 'text', text: 'Hello' },
    ]);

    expect(getVideoUrls(msg)).toEqual([]);
  });
});

// ============================================================
// normalizeMessage 测试
// ============================================================

describe('normalizeMessage', () => {
  it('should pass through array content', () => {
    const msg = {
      id: 'msg-1',
      content: [{ type: 'text', text: 'Hello' }],
      status: 'completed',
    };

    const result = normalizeMessage(msg);

    expect(result.content).toEqual([{ type: 'text', text: 'Hello' }]);
  });

  it('should convert string content to array', () => {
    const msg = {
      id: 'msg-1',
      content: 'Hello World',
    };

    const result = normalizeMessage(msg);

    expect(result.content).toEqual([{ type: 'text', text: 'Hello World' }]);
  });

  it('should parse JSON string content', () => {
    const msg = {
      id: 'msg-1',
      content: JSON.stringify([{ type: 'text', text: 'Parsed' }]),
    };

    const result = normalizeMessage(msg);

    expect(result.content).toEqual([{ type: 'text', text: 'Parsed' }]);
  });

  it('should set status from is_error flag', () => {
    const msg = {
      id: 'msg-1',
      content: [],
      is_error: true,
    };

    const result = normalizeMessage(msg);

    expect(result.status).toBe('failed');
  });
});

// ============================================================
// getFiles
// ============================================================

describe('getFiles', () => {
  const makeMsg = (content: ContentPart[]): Message => ({
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant' as const,
    content,
    status: 'completed' as const,
    created_at: '2026-04-06',
  });

  it('should extract FilePart from content', () => {
    const result = getFiles(makeMsg([
      { type: 'text', text: '报表已生成' },
      { type: 'file', url: 'https://cdn.example.com/a.xlsx', name: '报表.xlsx', mime_type: 'application/vnd.ms-excel', size: 2048 },
    ]));
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('报表.xlsx');
    expect(result[0].size).toBe(2048);
  });

  it('should return empty array when no FilePart', () => {
    const result = getFiles(makeMsg([
      { type: 'text', text: '普通文字' },
      { type: 'image', url: 'https://cdn.example.com/img.png' },
    ]));
    expect(result).toHaveLength(0);
  });

  it('should filter out FilePart with empty url', () => {
    const result = getFiles(makeMsg([
      { type: 'file', url: '', name: 'empty.xlsx', mime_type: 'application/vnd.ms-excel' },
    ]));
    expect(result).toHaveLength(0);
  });

  it('should handle non-array content', () => {
    const msg = { id: 'msg-1', content: 'old string format' } as unknown as Message;
    const result = getFiles(msg);
    expect(result).toHaveLength(0);
  });

  it('should extract multiple FileParts', () => {
    const result = getFiles(makeMsg([
      { type: 'file', url: 'https://a.com/1.csv', name: 'a.csv', mime_type: 'text/csv', size: 100 },
      { type: 'text', text: '中间文字' },
      { type: 'file', url: 'https://a.com/2.xlsx', name: 'b.xlsx', mime_type: 'application/vnd.ms-excel', size: 200 },
    ]));
    expect(result).toHaveLength(2);
  });
});

// ============================================================
// calcRemainingText 测试
// ============================================================

describe('calcRemainingText', () => {
  it('should extract remaining text after blocks text', () => {
    const blocks = [
      { type: 'text', text: 'turn1' },
      { type: 'tool_step', tool_name: 'data_query' },
    ];
    const result = calcRemainingText(blocks, 'turn1turn2 answer');
    expect(result).toBe('turn2 answer');
  });

  it('should return empty string when text matches blocks exactly', () => {
    const blocks = [{ type: 'text', text: 'all text' }];
    const result = calcRemainingText(blocks, 'all text');
    expect(result).toBe('');
  });

  it('should return full accumulated when text does not start with blocks text', () => {
    const blocks = [{ type: 'text', text: 'original' }];
    const result = calcRemainingText(blocks, 'completely different');
    expect(result).toBe('completely different');
  });

  it('should return empty string for null accumulated', () => {
    const blocks = [{ type: 'text', text: 'hello' }];
    const result = calcRemainingText(blocks, null);
    expect(result).toBe('');
  });

  it('should return empty string for undefined accumulated', () => {
    const blocks = [{ type: 'text', text: 'hello' }];
    const result = calcRemainingText(blocks, undefined);
    expect(result).toBe('');
  });

  it('should handle blocks with no text parts', () => {
    const blocks = [
      { type: 'tool_step', tool_name: 'data_query' },
      { type: 'tool_step', tool_name: 'code_execute' },
    ];
    const result = calcRemainingText(blocks, 'some text');
    // blocksText is '', accumulated starts with '' → remaining = 'some text'
    expect(result).toBe('some text');
  });

  it('should handle multiple text blocks in order', () => {
    const blocks = [
      { type: 'text', text: 'a' },
      { type: 'tool_step', tool_name: 't1' },
      { type: 'text', text: 'b' },
      { type: 'tool_step', tool_name: 't2' },
    ];
    const result = calcRemainingText(blocks, 'abfinal');
    expect(result).toBe('final');
  });

  it('should handle empty blocks array', () => {
    const result = calcRemainingText([], 'text');
    // blocksText is '', accumulated starts with '' → remaining = 'text'
    expect(result).toBe('text');
  });
});
