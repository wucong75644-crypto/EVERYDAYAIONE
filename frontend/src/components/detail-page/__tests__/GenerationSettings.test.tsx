import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DEFAULT_DETAIL_FORM } from '../../../mocks/detailPageMocks';
import { GenerationSettings } from '../GenerationSettings';

describe('GenerationSettings', () => {
  it('使用中文、1K和1张默认值', () => {
    render(<GenerationSettings form={DEFAULT_DETAIL_FORM} hasProductImage={false} onChange={vi.fn()} onAnalyze={vi.fn()} />);
    expect(screen.getByRole('button', { name: '目标语言' })).toHaveTextContent('中文（简体）');
    expect(screen.getByRole('button', { name: '清晰度' })).toHaveTextContent('1K 标准');
    expect(screen.getByRole('button', { name: '生成数量' })).toHaveTextContent('1 张');
    expect(screen.getByRole('button', { name: '分析产品' })).toBeDisabled();
  });

  it('切换详情图和 AI 帮写时提交表单更新', () => {
    const onChange = vi.fn();
    render(<GenerationSettings form={DEFAULT_DETAIL_FORM} hasProductImage onChange={onChange} onAnalyze={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '详情图' }));
    expect(onChange).toHaveBeenCalledWith({ contentType: 'detail_page' });
    fireEvent.click(screen.getByRole('button', { name: 'AI 帮写' }));
    expect(onChange).toHaveBeenLastCalledWith({ requirement: expect.stringContaining('核心卖点') });
  });

  it('有产品图时允许分析', () => {
    const onAnalyze = vi.fn();
    render(<GenerationSettings form={DEFAULT_DETAIL_FORM} hasProductImage onChange={vi.fn()} onAnalyze={onAnalyze} />);
    fireEvent.click(screen.getByRole('button', { name: '分析产品' }));
    expect(onAnalyze).toHaveBeenCalledOnce();
  });

  it('所有下拉设置均提交对应字段', () => {
    const onChange = vi.fn();
    render(<GenerationSettings form={DEFAULT_DETAIL_FORM} hasProductImage onChange={onChange} onAnalyze={vi.fn()} />);

    for (const [field, option] of [['目标平台', '京东'], ['目标语言', '无文字'], ['尺寸比例', '4:5'], ['清晰度', '2K 高清'], ['生成数量', '9 张']]) {
      fireEvent.keyDown(screen.getByRole('button', { name: field }), { key: 'ArrowDown' });
      fireEvent.click(screen.getByText(option));
    }

    expect(onChange).toHaveBeenCalledWith({ platform: 'jd' });
    expect(onChange).toHaveBeenCalledWith({ language: 'none' });
    expect(onChange).toHaveBeenCalledWith({ aspectRatio: '4:5' });
    expect(onChange).toHaveBeenCalledWith({ quality: '2k' });
    expect(onChange).toHaveBeenCalledWith({ count: 9 });
  });

  it('详情图状态显示详情图要求并可整体禁用', () => {
    const detailForm = { ...DEFAULT_DETAIL_FORM, contentType: 'detail_page' as const, aspectRatio: '3:4' };
    render(<GenerationSettings form={detailForm} hasProductImage disabled onChange={vi.fn()} onAnalyze={vi.fn()} />);
    expect(screen.getByLabelText('详情图要求')).toBeDisabled();
    expect(screen.getByRole('button', { name: '尺寸比例' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '分析产品' })).toBeDisabled();
  });
});
