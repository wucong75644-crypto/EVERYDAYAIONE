import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RequirementAssistModal } from '../RequirementAssistModal';
import type { RequirementAssistResult } from '../../../types/ecomRequirement';

const result: RequirementAssistResult = {
  product_facts: {
    product_name: '风景活页笔记本',
    confirmed_attributes: ['200页', '活页装订'],
    unclear_items: ['封面材质未明确'],
  },
  reference_analyses: [{
    image_id: 'r1', primary_uses: ['background'],
    summary: '浅色自然背景，主体居中', excluded_elements: ['参考商品'],
  }],
  conflicts: [{
    field: '页数', user_value: '400页', confirmed_value: '200页',
    message: '页数待确认，当前不可作为卖点', blocked_claims: ['400页'],
  }],
  suggestions: [
    { id: 'selling_point', name: '卖点型', style_name: '清新风', brief_markdown: '卖点简报' },
    { id: 'scene', name: '场景型', style_name: '生活风', brief_markdown: '场景简报' },
    { id: 'creative', name: '创意型', style_name: '创意风', brief_markdown: '创意简报' },
  ],
};

const renderModal = (overrides = {}) => {
  const props = {
    isOpen: true, isLoading: false, result,
    selectedId: 'selling_point' as const, selectedBrief: '卖点简报', error: null,
    onClose: vi.fn(), onSelect: vi.fn(), onDraftChange: vi.fn(),
    onRegenerate: vi.fn(), onConfirm: vi.fn(), ...overrides,
  };
  render(<RequirementAssistModal {...props} />);
  return props;
};

describe('RequirementAssistModal', () => {
  it('首次请求显示产品分析加载状态', () => {
    renderModal({ isLoading: true, result: null, selectedBrief: '' });
    expect(screen.getByText('正在分析产品图片…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认选择' })).toBeDisabled();
  });

  it('展示产品事实、参考图理解和冲突', () => {
    renderModal();
    expect(screen.getByText('风景活页笔记本')).toBeInTheDocument();
    expect(screen.getByText('浅色自然背景，主体居中')).toBeInTheDocument();
    expect(screen.getByText(/页数待确认/)).toBeInTheDocument();
    expect(screen.getByText(/封面材质未明确/)).toBeInTheDocument();
  });

  it('切换方案和编辑内容分别触发回调', () => {
    const props = renderModal();
    fireEvent.click(screen.getByRole('tab', { name: '场景型' }));
    fireEvent.change(screen.getByLabelText('当前方案创作简报'), { target: { value: '新的卖点简报' } });
    expect(props.onSelect).toHaveBeenCalledWith('scene');
    expect(props.onDraftChange).toHaveBeenCalledWith('selling_point', '新的卖点简报');
  });

  it('确认时提交当前编辑后的简报', () => {
    const props = renderModal({ selectedBrief: '用户编辑后的方案' });
    fireEvent.click(screen.getByRole('button', { name: '确认选择' }));
    expect(props.onConfirm).toHaveBeenCalledWith('用户编辑后的方案');
  });

  it('重新帮写失败时旧方案和错误同时保留', () => {
    const props = renderModal({ error: '模型暂时不可用' });
    expect(screen.getByRole('alert')).toHaveTextContent('模型暂时不可用');
    expect(screen.getByDisplayValue('卖点简报')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新帮写' }));
    expect(props.onRegenerate).toHaveBeenCalled();
  });
});
