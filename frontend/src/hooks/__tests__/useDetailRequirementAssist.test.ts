import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useDetailRequirementAssist } from '../useDetailRequirementAssist';
import { generateRequirementSuggestions } from '../../services/ecomRequirement';
import type { DetailGenerationForm } from '../../types/detailPage';
import type { RequirementSuggestionsEnvelope } from '../../types/ecomRequirement';


vi.mock('../../services/ecomRequirement', () => ({ generateRequirementSuggestions: vi.fn() }));

const form: DetailGenerationForm = {
  contentType: 'main_image', platform: 'taobao', requirement: '清新自然',
  language: 'zh-CN', aspectRatio: '1:1', quality: '1k', count: 5,
};

const response = (): RequirementSuggestionsEnvelope => ({
  success: true,
  data: {
    product_facts: { product_name: '笔记本', confirmed_attributes: ['200页'], unclear_items: [] },
    reference_analyses: [],
    conflicts: [],
    suggestions: [
      { id: 'selling_point', name: '卖点型', style_name: '清新风', brief_markdown: '卖点方案' },
      { id: 'scene', name: '场景型', style_name: '生活风', brief_markdown: '场景方案' },
      { id: 'creative', name: '创意型', style_name: '创意风', brief_markdown: '创意方案' },
    ],
  },
  error: null,
  meta: { model: 'qwen-vl-max', fallback_used: false, latency_ms: 1000, project_version: 2 },
});


beforeEach(() => vi.mocked(generateRequirementSuggestions).mockReset());


describe('useDetailRequirementAssist', () => {
  it('打开后加载三套方案并默认选择第一套', async () => {
    vi.mocked(generateRequirementSuggestions).mockResolvedValue(response());
    const { result } = renderHook(() => useDetailRequirementAssist());

    await act(async () => result.current.open('project-1', form));

    expect(result.current.isOpen).toBe(true);
    expect(result.current.status).toBe('success');
    expect(result.current.selectedId).toBe('selling_point');
    expect(result.current.selectedBrief).toBe('卖点方案');
  });

  it('三套方案分别保留用户编辑内容', async () => {
    vi.mocked(generateRequirementSuggestions).mockResolvedValue(response());
    const { result } = renderHook(() => useDetailRequirementAssist());
    await act(async () => result.current.open('project-1', form));

    act(() => {
      result.current.updateDraft('selling_point', '修改后的卖点');
      result.current.selectSuggestion('scene');
      result.current.updateDraft('scene', '修改后的场景');
    });

    expect(result.current.drafts.selling_point).toBe('修改后的卖点');
    expect(result.current.selectedBrief).toBe('修改后的场景');
  });

  it('重新帮写失败时保留旧方案和编辑内容', async () => {
    vi.mocked(generateRequirementSuggestions)
      .mockResolvedValueOnce(response())
      .mockRejectedValueOnce(new Error('模型暂时不可用'));
    const { result } = renderHook(() => useDetailRequirementAssist());
    await act(async () => result.current.open('project-1', form));
    act(() => result.current.updateDraft('selling_point', '保留我的编辑'));

    await act(async () => result.current.regenerate());

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('模型暂时不可用');
    expect(result.current.result?.product_facts.product_name).toBe('笔记本');
    expect(result.current.drafts.selling_point).toBe('保留我的编辑');
  });

  it('新请求结果不会被较晚返回的旧请求覆盖', async () => {
    let resolveFirst: (value: RequirementSuggestionsEnvelope) => void = () => undefined;
    const first = new Promise<RequirementSuggestionsEnvelope>((resolve) => { resolveFirst = resolve; });
    const secondResponse = response();
    secondResponse.data.product_facts.product_name = '新结果';
    vi.mocked(generateRequirementSuggestions)
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(secondResponse);
    const { result } = renderHook(() => useDetailRequirementAssist());

    let firstOpen: Promise<void> = Promise.resolve();
    act(() => { firstOpen = result.current.open('project-1', form); });
    await act(async () => result.current.regenerate());
    await act(async () => resolveFirst(response()));
    await firstOpen;

    expect(result.current.result?.product_facts.product_name).toBe('新结果');
  });

  it('关闭弹窗会中止正在进行的请求', async () => {
    let finishRequest: (value: RequirementSuggestionsEnvelope) => void = () => undefined;
    vi.mocked(generateRequirementSuggestions).mockReturnValue(
      new Promise((resolve) => { finishRequest = resolve; }),
    );
    const { result } = renderHook(() => useDetailRequirementAssist());
    act(() => { void result.current.open('project-1', form); });
    expect(generateRequirementSuggestions).toHaveBeenCalledOnce();
    const signal = vi.mocked(generateRequirementSuggestions).mock.calls[0][2];

    act(() => result.current.close());

    expect(signal?.aborted).toBe(true);
    expect(result.current.isOpen).toBe(false);
    await act(async () => finishRequest(response()));
  });
});
