import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RequirementAssistModal } from '../RequirementAssistModal';
import { assistDraft } from '../../../test/fixtures/requirementAssist';

function renderModal(overrides={}) {
  const props={
    isOpen:true,isLoading:false,draft:assistDraft(),brief:'一份人工确认的简报',error:null,validationError:null,
    supplement:'',answers:{},skippedQuestions:[],
    onClose:vi.fn(),onDraftChange:vi.fn(),onSupplementChange:vi.fn(),onAnswer:vi.fn(),
    onToggleSkip:vi.fn(),onUpdate:vi.fn(),onConfirm:vi.fn(),...overrides,
  };
  render(<RequirementAssistModal {...props}/>);
  return props;
}
describe('单份AI帮写弹窗',()=>{
  it('首次分析提示使用图片和文字，加载期间不能采用',()=>{
    renderModal({isLoading:true,draft:null});
    expect(screen.getByText('正在分析图片和文字…')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'采用内容'})).toBeDisabled();
  });
  it('只显示一份草稿，标明推断，并可编辑产品、卖点和风格',()=>{
    const props=renderModal();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByText('AI 建议 · 请核验')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('产品细节与规格'),{target:{value:'人工修改产品'}});
    expect(props.onDraftChange).toHaveBeenCalledWith({product_description:'人工修改产品'});
    fireEvent.change(screen.getByLabelText('卖点1价值'),{target:{value:'合理卖点'}});
    expect(props.onDraftChange).toHaveBeenLastCalledWith({selling_points:[expect.objectContaining({benefit:'合理卖点'})]});
    fireEvent.change(screen.getByLabelText('背景 · 用户要求'),{target:{value:'新背景'}});
    expect(props.onDraftChange).toHaveBeenLastCalledWith({creative_requirements:[expect.objectContaining({text:'新背景'})]});
  });
  it('回答和跳过问题均可操作，输入不会自动更新',()=>{
    const props=renderModal();
    fireEvent.change(screen.getByLabelText('本体尺寸是多少？'),{target:{value:'20cm'}});
    expect(props.onAnswer).toHaveBeenCalledWith('本体尺寸是多少？','20cm');
    fireEvent.click(screen.getAllByRole('button',{name:'暂不知道 / 跳过'})[1]);
    expect(props.onToggleSkip).toHaveBeenCalledWith('封面材质是什么？');
    fireEvent.change(screen.getByLabelText('其他补充或修改方向'),{target:{value:'自然光'}});
    expect(props.onSupplementChange).toHaveBeenCalledWith('自然光');
    expect(props.onUpdate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'更新草稿'}));
    expect(props.onUpdate).toHaveBeenCalledOnce();
  });
  it('可补充删除卖点，采用时交付单份拼接内容',()=>{
    const props=renderModal();
    fireEvent.click(screen.getByRole('button',{name:'补充卖点'}));
    expect(props.onDraftChange).toHaveBeenCalledWith({selling_points:[expect.anything(),{feature:'',benefit:'',benefit_basis:'inferred'}]});
    fireEvent.click(screen.getByRole('button',{name:'删除卖点1'}));
    expect(props.onDraftChange).toHaveBeenLastCalledWith({selling_points:[]});
    fireEvent.click(screen.getByRole('button',{name:'采用内容'}));
    expect(props.onConfirm).toHaveBeenCalledWith('一份人工确认的简报');
  });
  it('失败时旧编辑稿和错误同时显示，仍可修改后采用',()=>{
    renderModal({error:'Kimi暂时不可用'});
    expect(screen.getByRole('alert')).toHaveTextContent('Kimi暂时不可用');
    expect(screen.getByDisplayValue('红色存钱本，普通印刷，侧边搭扣')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'采用内容'})).toBeEnabled();
  });
});
