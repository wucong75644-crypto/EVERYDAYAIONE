/**
 * placeholder.ts 单元测试
 *
 * 覆盖：RENDER_CONFIG、getPlaceholderText、getCompletedBubbleText、
 *       getPlaceholderInfo、getAgentStepText、isMediaPlaceholder
 */

import { describe, expect, it, vi } from 'vitest';

import {
  PLACEHOLDER_TEXT,
  RENDER_CONFIG,
  getPlaceholderText,
  getCompletedBubbleText,
  getPlaceholderInfo,
  getAgentStepText,
  isMediaPlaceholder,
  type MessageType,
} from '../placeholder';
import type { Message } from '../../stores/useMessageStore';

// ============================================================
// Helpers
// ============================================================

/** 创建最小 Message 对象 */
function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'test-msg',
    role: 'assistant',
    content: [],
    status: 'completed',
    ...overrides,
  } as Message;
}

/** 创建带文字的 Message */
function makeTextMessage(text: string, overrides: Partial<Message> = {}): Message {
  return makeMessage({
    content: [{ type: 'text', text }],
    ...overrides,
  });
}

// ============================================================
// RENDER_CONFIG
// ============================================================

describe('RENDER_CONFIG', () => {
  const mediaTypes: Array<Exclude<MessageType, 'chat'>> = [
    'image', 'video', 'audio', '3d', 'code',
  ];

  it.each(mediaTypes)('%s has loadingText and completedText', (type) => {
    expect(RENDER_CONFIG[type]).toBeDefined();
    expect(RENDER_CONFIG[type].loadingText).toBeTruthy();
    expect(RENDER_CONFIG[type].completedText).toBeTruthy();
  });
});

// ============================================================
// getPlaceholderText
// ============================================================

describe('getPlaceholderText', () => {
  it('chat → "AI 正在思考"', () => {
    expect(getPlaceholderText('chat')).toBe('AI 正在思考');
  });

  it('image → "图片生成中"', () => {
    expect(getPlaceholderText('image')).toBe('图片生成中');
  });

  it('video → "视频生成中"', () => {
    expect(getPlaceholderText('video')).toBe('视频生成中');
  });
});

// ============================================================
// getCompletedBubbleText
// ============================================================

describe('getCompletedBubbleText', () => {
  it('chat → ""', () => {
    expect(getCompletedBubbleText('chat')).toBe('');
  });

  it('image 单张 → "好的，来看看生成的图片"', () => {
    expect(getCompletedBubbleText('image', 1)).toBe('好的，来看看生成的图片');
  });

  it('image 3张 → "好的，来看看生成的 3 张图片"', () => {
    expect(getCompletedBubbleText('image', 3)).toBe('好的，来看看生成的 3 张图片');
  });

  it('video → "生成完成"（无复数形式）', () => {
    expect(getCompletedBubbleText('video', 1)).toBe('生成完成');
    expect(getCompletedBubbleText('video', 3)).toBe('生成完成');
  });

  it('count=0 → 单数形式', () => {
    expect(getCompletedBubbleText('image', 0)).toBe('好的，来看看生成的图片');
  });

  it('count=undefined → 单数形式', () => {
    expect(getCompletedBubbleText('image')).toBe('好的，来看看生成的图片');
  });
});

// ============================================================
// getPlaceholderInfo
// ============================================================

describe('getPlaceholderInfo', () => {
  it('generation_params.type=image + pending → isPlaceholder:true', () => {
    const msg = makeTextMessage('图片生成中', {
      status: 'pending',
      generation_params: { type: 'image' },
    });
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(true);
    expect(info.type).toBe('image');
  });

  it('_render.placeholder_text 覆盖默认文字', () => {
    const msg = makeTextMessage('', {
      status: 'pending',
      generation_params: {
        type: 'image',
        _render: { placeholder_text: '自定义生成中' },
      },
    });
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(true);
    expect(info.text).toBe('自定义生成中');
  });

  it('streaming + 空内容 → chat 占位符', () => {
    const msg = makeMessage({
      content: [],
      status: 'streaming',
    });
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(true);
    expect(info.type).toBe('chat');
    expect(info.text).toBe(PLACEHOLDER_TEXT.CHAT_THINKING);
  });

  it('completed → 非占位符', () => {
    const msg = makeTextMessage('完整回复内容', {
      status: 'completed',
      generation_params: { type: 'image' },
    });
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(false);
  });

  it('Legacy 文字匹配 "图片生成中" → image', () => {
    const msg = makeTextMessage('图片生成中', {
      status: 'completed',
    });
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(true);
    expect(info.type).toBe('image');
  });

  it('普通文字 → 非占位符', () => {
    const msg = makeTextMessage('你好，这是普通回复');
    const info = getPlaceholderInfo(msg);
    expect(info.isPlaceholder).toBe(false);
  });

  it('chat type + pending → 非占位符（chat 不走媒体路径）', () => {
    const msg = makeTextMessage('', {
      status: 'pending',
      generation_params: { type: 'chat' },
    });
    const info = getPlaceholderInfo(msg);
    // chat type 不满足 genType !== 'chat'，所以不走 Priority 1
    // 空内容 + pending 不是 streaming，所以也不走 Priority 2
    expect(info.isPlaceholder).toBe(false);
  });
});

// ============================================================
// getAgentStepText
// ============================================================

describe('getAgentStepText', () => {
  it('web_search → "正在搜索"', () => {
    expect(getAgentStepText('web_search')).toBe('正在搜索');
  });

  it('get_conversation_context → "正在查看对话"', () => {
    expect(getAgentStepText('get_conversation_context')).toBe('正在查看对话');
  });

  it('unknown → "AI 正在分析"', () => {
    expect(getAgentStepText('unknown_tool')).toBe('AI 正在分析');
  });
});

// ============================================================
// isMediaPlaceholder
// ============================================================

describe('isMediaPlaceholder', () => {
  it('image 占位符 → true', () => {
    const msg = makeTextMessage('图片生成中', {
      status: 'pending',
      generation_params: { type: 'image' },
    });
    expect(isMediaPlaceholder(msg)).toBe(true);
  });

  it('chat 占位符 → false', () => {
    const msg = makeMessage({
      content: [],
      status: 'streaming',
    });
    expect(isMediaPlaceholder(msg)).toBe(false);
  });

  it('非占位符 → false', () => {
    const msg = makeTextMessage('普通消息');
    expect(isMediaPlaceholder(msg)).toBe(false);
  });
});
