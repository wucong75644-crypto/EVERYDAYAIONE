/**
 * EcomPlanCards — syncTextToPrompt 纯函数测试
 *
 * 验证用户编辑文案后同步到 prompt 中引号位置的逻辑。
 * 与后端 sync_text_to_prompt 保持一致。
 */

import { describe, it, expect } from 'vitest';
import { syncTextToPrompt } from '../EcomPlanCards';

describe('syncTextToPrompt', () => {
  it('should replace both title and subtitle', () => {
    const prompt = 'Preserve... Bold title "一盒搞定" in white. Subtitle "56色分类收纳" in gray.';
    const result = syncTextToPrompt(prompt, '大容量', '装下200瓶');
    expect(result).toContain('"大容量"');
    expect(result).toContain('"装下200瓶"');
    expect(result).not.toContain('一盒搞定');
    expect(result).not.toContain('56色分类收纳');
  });

  it('should replace title only when one Chinese match exists', () => {
    const prompt = 'Preserve... title "一盒搞定" in white. No subtitle.';
    const result = syncTextToPrompt(prompt, '新标题', '');
    expect(result).toContain('"新标题"');
    expect(result).not.toContain('一盒搞定');
  });

  it('should not modify prompt with no Chinese text in quotes', () => {
    const prompt = 'Pure white background. No text. No watermark.';
    const result = syncTextToPrompt(prompt, '测试', '测试');
    expect(result).toBe(prompt);
  });

  it('should not modify when both new values are empty', () => {
    const prompt = 'title "一盒搞定" in white.';
    const result = syncTextToPrompt(prompt, '', '');
    expect(result).toBe(prompt);
  });

  it('should handle prompt with three or more Chinese quoted strings', () => {
    const prompt = 'title "限时特惠" bold. sub "前100名送色卡" light. price "¥39.9元" yellow.';
    const result = syncTextToPrompt(prompt, '新年大促', '买一送一');
    // 只替换前两个
    expect(result).toContain('"新年大促"');
    expect(result).toContain('"买一送一"');
    // 第三个不变
    expect(result).toContain('"¥39.9元"');
  });
});
