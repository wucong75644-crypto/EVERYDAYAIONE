import { describe, expect, it } from 'vitest';
import { assistDraft } from '../../test/fixtures/requirementAssist';
import { buildSupplementText,draftValidationError,formatRequirementDraft } from '../requirementAssist';
describe('单份草稿拼接',()=>{
  it('原文、人工确认资料和推断标记保持，跳过内容不当成产品事实',()=>{
    const draft=assistDraft();
    const brief=formatRequirementDraft(draft,'  用户原文\n原格式  ',['20×14×3cm','普通印刷'],['材质？']);
    expect(brief).toContain('  用户原文\n原格式  ');
    expect(brief).toContain('20×14×3cm\n\n普通印刷');
    expect(brief).toContain('当前人工确认资料');
    expect(brief).toContain('AI合理推断，经用户审核采用');
    expect(brief).toContain('保持未知，不作为产品事实');
    expect(brief).not.toContain('本体尺寸是多少？');
  });
  it('只提交回答过且未跳过的当前问题，并附自由补充',()=>{
    const text=buildSupplementText(assistDraft(),{'本体尺寸是多少？':'20cm','封面材质是什么？':'旧答案'},['封面材质是什么？'],'米白背景');
    expect(text).toContain('20cm');expect(text).toContain('米白背景');expect(text).not.toContain('旧答案');
  });
  it('新增卖点空白项提示补全，不触发一次模型重写',()=>{
    const draft=assistDraft();draft.selling_points.push({feature:'',benefit:'',benefit_basis:'inferred'});
    expect(draftValidationError(draft)).toContain('补全卖点');
  });
  it('客户本次补充可直接拼入，并优先于草稿中的旧要求',()=>{
    const draft=assistDraft();
    const brief=formatRequirementDraft(draft,'原始要求',['历史补充'],[], '背景改为深蓝，尺寸20×14cm');
    expect(brief).toContain('本次客户补充（最新内容，与上文有差异时以此为准）');
    expect(brief.indexOf('背景改为深蓝')).toBeGreaterThan(brief.indexOf('米白背景'));
  });
});
