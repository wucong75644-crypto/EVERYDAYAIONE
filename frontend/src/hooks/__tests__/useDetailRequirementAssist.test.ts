import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useDetailRequirementAssist } from '../useDetailRequirementAssist';
import { generateRequirementSuggestions } from '../../services/ecomRequirement';
import { assistResponse } from '../../test/fixtures/requirementAssist';
import type { DetailGenerationForm } from '../../types/detailPage';
import type { RequirementSuggestionsEnvelope } from '../../types/ecomRequirement';
vi.mock('../../services/ecomRequirement',()=>({generateRequirementSuggestions:vi.fn()}));
const form: DetailGenerationForm={contentType:'default',platform:'taobao',requirement:'  原始文字\n发财感觉  ',language:'zh-CN',aspectRatio:'1:1',quality:'1k',count:14};
beforeEach(()=>vi.mocked(generateRequirementSuggestions).mockReset());

describe('单份AI帮写草稿',()=>{
  it('图片项目和文字快照发给接口，生成后保留原文并编译一份简报',async()=>{
    vi.mocked(generateRequirementSuggestions).mockResolvedValue(assistResponse());
    const {result}=renderHook(()=>useDetailRequirementAssist());
    await act(async()=>result.current.open('project-1',form));
    expect(generateRequirementSuggestions).toHaveBeenCalledWith('project-1',form,expect.any(AbortSignal),undefined);
    expect(result.current.draft?.product_description).toContain('存钱本');
    expect(result.current.brief).toContain(form.requirement);
    expect(result.current.brief).toContain('送礼有仪式感');
    expect(result.current.brief).toContain('米白背景');
  });
  it('编辑和回答不自动请求，点击更新才携带人工稿、回答与跳过项',async()=>{
    vi.mocked(generateRequirementSuggestions).mockResolvedValue(assistResponse());
    const {result}=renderHook(()=>useDetailRequirementAssist());
    await act(async()=>result.current.open('project-1',form));
    act(()=>{
      result.current.updateDraft({product_description:'人工修正：不是烫金'});
      result.current.answerQuestion('本体尺寸是多少？','20×14×3cm');
      result.current.toggleSkip('封面材质是什么？');
      result.current.setSupplement('不要元宝，改成米白背景');
    });
    expect(generateRequirementSuggestions).toHaveBeenCalledTimes(1);
    expect(result.current.validationError).toBeNull();
    expect(result.current.brief).toContain('本次客户补充（最新内容，与上文有差异时以此为准）');
    expect(result.current.brief).toContain('20×14×3cm');
    expect(result.current.brief.lastIndexOf('不要元宝，改成米白背景')).toBeGreaterThan(result.current.brief.indexOf('人工修正：不是烫金'));
    await act(async()=>result.current.update());
    const revision=vi.mocked(generateRequirementSuggestions).mock.calls[1][3];
    expect(revision?.draft.product_description).toBe('人工修正：不是烫金');
    expect(revision?.supplement).toContain('20×14×3cm');
    expect(revision?.supplement).toContain('不要元宝');
    expect(revision?.skipped_questions).toEqual(['封面材质是什么？']);
    expect(result.current.brief).toContain(form.requirement);
    expect(result.current.brief).toContain('20×14×3cm');
    expect(result.current.validationError).toBeNull();
  });
  it('更新失败保留人工编辑、补充答案及旧草稿',async()=>{
    vi.mocked(generateRequirementSuggestions).mockResolvedValueOnce(assistResponse()).mockRejectedValueOnce(new Error('Kimi暂时不可用'));
    const {result}=renderHook(()=>useDetailRequirementAssist());
    await act(async()=>result.current.open('project-1',form));
    act(()=>{
      result.current.updateDraft({product_description:'保留我的编辑'});
      result.current.setSupplement('米白背景');
      result.current.answerQuestion('本体尺寸是多少？','20cm');
    });
    await act(async()=>result.current.update());
    expect(result.current.error).toBe('Kimi暂时不可用');
    expect(result.current.draft?.product_description).toBe('保留我的编辑');
    expect(result.current.supplement).toBe('米白背景');
    expect(result.current.answers['本体尺寸是多少？']).toBe('20cm');
  });
  it('新响应不被较晚返回的旧响应覆盖',async()=>{
    let resolveFirst!:(value:RequirementSuggestionsEnvelope)=>void;
    const first=new Promise<RequirementSuggestionsEnvelope>(resolve=>{resolveFirst=resolve;});
    const newer=assistResponse();newer.data.product_description='新结果';
    vi.mocked(generateRequirementSuggestions).mockReturnValueOnce(first).mockResolvedValueOnce(newer);
    const {result}=renderHook(()=>useDetailRequirementAssist());
    let initial!:Promise<void>;
    act(()=>{initial=result.current.open('project-1',form);});
    await act(async()=>result.current.update());
    await act(async()=>{resolveFirst(assistResponse());await initial;});
    expect(result.current.draft?.product_description).toBe('新结果');
  });
  it('关闭会中止请求，并忽略之后返回的响应',async()=>{
    let finish!:(value:RequirementSuggestionsEnvelope)=>void;
    vi.mocked(generateRequirementSuggestions).mockReturnValue(new Promise(resolve=>{finish=resolve;}));
    const {result}=renderHook(()=>useDetailRequirementAssist());
    act(()=>{void result.current.open('project-1',form);});
    const signal=vi.mocked(generateRequirementSuggestions).mock.calls[0][2];
    act(()=>result.current.close());
    expect(signal?.aborted).toBe(true);
    await act(async()=>finish(assistResponse()));
    expect(result.current.isOpen).toBe(false);
    expect(result.current.draft).toBeNull();
  });
  it('超过持久化限额时提示精简，不截断原文',async()=>{
    vi.mocked(generateRequirementSuggestions).mockResolvedValue(assistResponse());
    const {result}=renderHook(()=>useDetailRequirementAssist());
    await act(async()=>result.current.open('project-1',{...form,requirement:'字'.repeat(10000)}));
    expect(result.current.validationError).toContain('超过10000');
    expect(result.current.brief).toContain('字'.repeat(10000));
  });
});
