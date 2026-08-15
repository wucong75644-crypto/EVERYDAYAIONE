/**
 * models.ts 单元测试
 *
 * 覆盖：模型ID唯一性、OpenRouter模型存在性、能力标记一致性、
 *       上下文窗口合理性、类型分类正确性
 */

import { describe, expect, it } from 'vitest';

import { ALL_MODELS, getAvailableModels, type UnifiedModel } from '../models';
import { CHAT_MODELS } from '../chatModels';
import { IMAGE_MODELS, VIDEO_MODELS } from '../mediaModels';
import { ASPECT_RATIOS, RESOLUTIONS, OUTPUT_FORMATS, IMAGE_COUNTS, VIDEO_DURATIONS, VIDEO_ASPECT_RATIOS } from '../mediaOptions';

// ============================================================
// 辅助函数
// ============================================================

function getChatModels(): UnifiedModel[] {
  return ALL_MODELS.filter((m) => m.type === 'chat');
}

function getOpenRouterModels(): UnifiedModel[] {
  return ALL_MODELS.filter(
    (m) => m.type === 'chat' && (m.id.includes('/') && m.id !== 'google/nano-banana' && m.id !== 'google/nano-banana-edit'),
  );
}

// ============================================================
// TestModelList
// ============================================================

describe('ALL_MODELS', () => {
  it('模型ID唯一', () => {
    const ids = ALL_MODELS.map((m) => m.id);
    const unique = new Set(ids);
    expect(ids.length).toBe(unique.size);
  });

  it('包含智能模型', () => {
    expect(ALL_MODELS[0].id).toBe('auto');
  });

  it('包含各类型模型', () => {
    const types = new Set(ALL_MODELS.map((m) => m.type));
    expect(types.has('chat')).toBe(true);
    expect(types.has('image')).toBe(true);
    expect(types.has('video')).toBe(true);
  });
});

// ============================================================
// TestOpenRouterModels
// ============================================================

describe('OpenRouter 模型', () => {
  const expectedIds = [
    'openai/gpt-4.1',
    'openai/gpt-4.1-mini',
    'openai/o4-mini',
    'anthropic/claude-sonnet-4',
    'x-ai/grok-4.1-fast',
    'openai/gpt-5.4',
    'openai/gpt-5.4-pro',
    'openai/gpt-5.3-codex',
    'google/gemini-3.1-pro-preview',
    'anthropic/claude-sonnet-4.6',
    'anthropic/claude-opus-4.6',
  ];

  it('包含所有 11 个 OpenRouter 模型', () => {
    const allIds = ALL_MODELS.map((m) => m.id);
    for (const id of expectedIds) {
      expect(allIds).toContain(id);
    }
  });

  it('OpenRouter 模型类型均为 chat', () => {
    for (const id of expectedIds) {
      const model = ALL_MODELS.find((m) => m.id === id);
      expect(model?.type).toBe('chat');
    }
  });

  it('OpenRouter 模型均支持 functionCalling', () => {
    for (const id of expectedIds) {
      const model = ALL_MODELS.find((m) => m.id === id);
      expect(model?.capabilities.functionCalling).toBe(true);
    }
  });

  it('OpenRouter 模型均支持 streamingResponse', () => {
    for (const id of expectedIds) {
      const model = ALL_MODELS.find((m) => m.id === id);
      expect(model?.capabilities.streamingResponse).toBe(true);
    }
  });

  it('视觉模型设置了 vqa=true', () => {
    const visionIds = [
      'openai/gpt-4.1', 'openai/gpt-5.4', 'anthropic/claude-sonnet-4.6',
      'anthropic/claude-opus-4.6',
    ];
    for (const id of visionIds) {
      const model = ALL_MODELS.find((m) => m.id === id);
      expect(model?.capabilities.vqa).toBe(true);
    }
  });

  it('Grok 不支持视觉', () => {
    const grok = ALL_MODELS.find((m) => m.id === 'x-ai/grok-4.1-fast');
    expect(grok?.capabilities.vqa).toBe(false);
  });

  it('上下文窗口合理（>100K tokens）', () => {
    for (const id of expectedIds) {
      const model = ALL_MODELS.find((m) => m.id === id);
      expect(model?.capabilities.maxContextTokens).toBeGreaterThanOrEqual(100000);
    }
  });
});

// ============================================================
// TestChatModels
// ============================================================

describe('聊天模型完整性', () => {
  it('聊天模型不少于 18 个（国内7 + KIE2 + Google2 + OpenRouter11 - 重叠）', () => {
    const chatModels = getChatModels();
    // auto + gemini-3-flash/pro + gemini-2.5-flash/pro + 5 dashscope + 11 openrouter = 20+
    expect(chatModels.length).toBeGreaterThanOrEqual(18);
  });

  it('每个聊天模型有 description', () => {
    for (const m of getChatModels()) {
      expect(m.description.length).toBeGreaterThan(0);
    }
  });

  it('每个聊天模型不生成图片/视频', () => {
    for (const m of getChatModels()) {
      if (m.id === 'auto') continue; // 智能模型跳过
      expect(m.capabilities.textToImage).toBe(false);
      expect(m.capabilities.textToVideo).toBe(false);
    }
  });
});

// ============================================================
// TestGetAvailableModels
// ============================================================

describe('getAvailableModels', () => {
  it('无 subscribedModelIds 参数时返回所有模型', () => {
    const models = getAvailableModels(false);
    expect(models.length).toBe(ALL_MODELS.length);
  });

  it('有图片时也返回所有模型（无订阅过滤）', () => {
    const models = getAvailableModels(true);
    expect(models.length).toBe(ALL_MODELS.length);
  });

  it('传入 subscribedModelIds 只返回已订阅模型 + auto', () => {
    const models = getAvailableModels(false, ['gemini-3-flash', 'deepseek-v3.2']);
    const ids = models.map((m) => m.id);
    expect(ids).toContain('auto');
    expect(ids).toContain('gemini-3-flash');
    expect(ids).toContain('deepseek-v3.2');
    expect(ids).not.toContain('gemini-3-pro');
  });

  it('auto 智能模式始终可用，即使不在订阅列表', () => {
    const models = getAvailableModels(false, ['gemini-3-flash']);
    const ids = models.map((m) => m.id);
    expect(ids).toContain('auto');
  });

  it('空订阅列表只返回 auto', () => {
    const models = getAvailableModels(false, []);
    expect(models.length).toBe(1);
    expect(models[0].id).toBe('auto');
  });

  it('undefined subscribedModelIds 等同于无过滤', () => {
    const models = getAvailableModels(false, undefined);
    expect(models.length).toBe(ALL_MODELS.length);
  });
});

// ============================================================
// TestModelSplit — 拆分后子模块验证
// ============================================================

describe('模型拆分子模块', () => {
  it('CHAT_MODELS 包含所有聊天模型', () => {
    const chatFromAll = ALL_MODELS.filter((m) => m.type === 'chat');
    expect(CHAT_MODELS.length).toBe(chatFromAll.length);
  });

  it('IMAGE_MODELS 包含所有图像模型', () => {
    const imageFromAll = ALL_MODELS.filter((m) => m.type === 'image');
    expect(IMAGE_MODELS.length).toBe(imageFromAll.length);
  });

  it('VIDEO_MODELS 包含所有视频模型', () => {
    const videoFromAll = ALL_MODELS.filter((m) => m.type === 'video');
    expect(VIDEO_MODELS.length).toBe(videoFromAll.length);
  });

  it('ALL_MODELS = CHAT + IMAGE + VIDEO', () => {
    expect(ALL_MODELS.length).toBe(
      CHAT_MODELS.length + IMAGE_MODELS.length + VIDEO_MODELS.length,
    );
  });

  it('IMAGE_MODELS 均支持图片生成', () => {
    for (const m of IMAGE_MODELS) {
      expect(m.capabilities.textToImage || m.capabilities.imageEditing).toBe(true);
    }
  });

  it('VIDEO_MODELS 均支持视频生成', () => {
    for (const m of VIDEO_MODELS) {
      expect(m.capabilities.textToVideo || m.capabilities.imageToVideo).toBe(true);
    }
  });

  it('VIDEO_MODELS 均有 videoPricing', () => {
    for (const m of VIDEO_MODELS) {
      expect(m.videoPricing).toBeDefined();
    }
  });
});

// ============================================================
// TestMediaOptions — 媒体选项常量验证
// ============================================================

describe('媒体选项常量', () => {
  it('ASPECT_RATIOS 非空且包含 1:1', () => {
    expect(ASPECT_RATIOS.length).toBeGreaterThan(0);
    expect(ASPECT_RATIOS.some((r) => r.value === '1:1')).toBe(true);
  });

  it('RESOLUTIONS 包含 1K/2K/4K', () => {
    const values = RESOLUTIONS.map((r) => r.value);
    expect(values).toContain('1K');
    expect(values).toContain('2K');
    expect(values).toContain('4K');
  });

  it('OUTPUT_FORMATS 包含 png 和 jpeg', () => {
    const values = OUTPUT_FORMATS.map((f) => f.value);
    expect(values).toContain('png');
    expect(values).toContain('jpeg');
  });

  it('IMAGE_COUNTS 包含 1-4', () => {
    const values = IMAGE_COUNTS.map((c) => c.value);
    expect(values).toEqual([1, 2, 3, 4]);
  });

  it('VIDEO_DURATIONS 非空', () => {
    expect(VIDEO_DURATIONS.length).toBeGreaterThan(0);
    expect(VIDEO_DURATIONS.every((d) => d.credits > 0)).toBe(true);
  });

  it('VIDEO_ASPECT_RATIOS 包含横竖屏', () => {
    const values = VIDEO_ASPECT_RATIOS.map((r) => r.value);
    expect(values).toContain('landscape');
    expect(values).toContain('portrait');
  });
});
