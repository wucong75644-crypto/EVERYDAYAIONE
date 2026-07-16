import { beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from '../api';
import {
  buildRequirementSuggestionsRequest,
  generateRequirementSuggestions,
} from '../ecomRequirement';
import type { DetailGenerationForm } from '../../types/detailPage';


vi.mock('../api', () => ({ request: vi.fn() }));

const form: DetailGenerationForm = {
  contentType: 'main_image', platform: 'taobao', requirement: '突出产品卖点',
  language: 'zh-CN', aspectRatio: '1:1', quality: '1k', count: 5,
};


beforeEach(() => vi.mocked(request).mockReset());


describe('ecomRequirement service', () => {
  it('将页面表单转换为后端设置快照', () => {
    expect(buildRequirementSuggestionsRequest('project-1', form)).toEqual({
      source: { type: 'detail_project', project_id: 'project-1' },
      settings: {
        content_type: 'main_image', platform: 'taobao', language: 'zh-CN',
        aspect_ratio: '1:1', quality: '1k', image_count: 5,
        requirement: '突出产品卖点',
      },
    });
  });

  it('调用三方案接口并使用独立长请求超时', async () => {
    const envelope = { success: true, data: { suggestions: [] }, error: null, meta: {} };
    vi.mocked(request).mockResolvedValue(envelope);

    await expect(generateRequirementSuggestions('project-1', form)).resolves.toBe(envelope);

    expect(request).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST', url: '/ecom-image/requirement-suggestions', timeout: 105_000,
    }));
  });

  it('透传 AbortSignal 用于取消关闭后的请求', async () => {
    vi.mocked(request).mockResolvedValue({});
    const controller = new AbortController();

    await generateRequirementSuggestions('project-1', form, controller.signal);

    expect(request).toHaveBeenCalledWith(expect.objectContaining({ signal: controller.signal }));
  });
});
