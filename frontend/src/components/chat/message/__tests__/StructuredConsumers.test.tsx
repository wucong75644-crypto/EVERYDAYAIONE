import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FormPart } from '../../../../types/message';
import FormBlock from '../FormBlock';
import { TableBlock } from '../TableBlock';

describe('structured message consumers', () => {
  it('TableBlock renders structured and circular cells without crashing', () => {
    const circular: { self?: unknown } = {};
    circular.self = circular;

    render(
      <TableBlock
        columns={['object', 'circular', 'bigint']}
        rows={[{ object: { answer: 42 }, circular, bigint: 10n }]}
      />,
    );

    expect(screen.getByText('{"answer":42}')).toBeInTheDocument();
    expect(screen.getByText('[无法显示的结构化数据]')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('TableBlock formats numeric, empty and truncated data', () => {
    render(
      <TableBlock
        title="统计"
        columns={['integer', 'decimal', 'empty']}
        rows={[{ integer: 1000, decimal: 1.234567, empty: null }]}
        truncated
      />,
    );
    expect(screen.getByText('统计')).toBeInTheDocument();
    expect(screen.getByText('1,000')).toBeInTheDocument();
    expect(screen.getByText(/1\.2346/)).toBeInTheDocument();
    expect(screen.getByText(/预览前 1 行/)).toBeInTheDocument();
  });

  it('TableBlock detects numeric and non-numeric string columns', () => {
    const { container } = render(
      <TableBlock
        columns={['numeric', 'text', 'blank']}
        rows={[{ numeric: '12.5', text: 'abc', blank: '' }]}
      />,
    );
    const headers = container.querySelectorAll('th');
    expect(headers[0]).toHaveClass('text-right');
    expect(headers[1]).toHaveClass('text-left');
    expect(headers[2]).toHaveClass('text-left');
  });

  it('FormBlock rejects a malformed structured scalar default', () => {
    const form = {
      type: 'form',
      form_type: 'test',
      form_id: 'form-1',
      fields: [{
        type: 'text',
        name: 'title',
        label: '标题',
        default_value: { unsafe: true },
      }],
    } as unknown as FormPart;

    render(<FormBlock form={form} />);

    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('FormBlock supports every field type and conditional visibility', () => {
    const form: FormPart = {
      type: 'form', form_type: 'schedule', form_id: 'form-all',
      title: '创建任务', description: '填写配置', submit_text: '保存', cancel_text: '放弃',
      fields: [
        { type: 'select', name: 'mode', label: '模式', default_value: 'daily', options: [
          { label: '每天', value: 'daily' }, { label: '每周', value: 'weekly' },
        ] },
        { type: 'text', name: 'name', label: '名称', required: true, placeholder: '任务名' },
        { type: 'textarea', name: 'note', label: '备注' },
        { type: 'time', name: 'time', label: '时间', default_value: '09:00' },
        { type: 'number', name: 'day', label: '日期', default_value: 1 },
        { type: 'checkbox_group', name: 'weeks', label: '星期', default_value: [1], options: [
          { label: '周一', value: '1' }, { label: '周二', value: '2' },
        ] },
        { type: 'text', name: 'weeklyName', label: '每周名称', visible_when: { field: 'mode', value: 'weekly' } },
        { type: 'hidden', name: 'token', label: '隐藏', default_value: 'secret' },
      ],
    };
    const { container } = render(<FormBlock form={form} />);

    expect(screen.getByText('创建任务')).toBeInTheDocument();
    expect(screen.getByText('填写配置')).toBeInTheDocument();
    expect(screen.queryByText('每周名称')).toBeNull();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'weekly' } });
    expect(screen.getByText('每周名称')).toBeInTheDocument();
    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: '新任务' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: '周二' }));
    fireEvent.click(screen.getByRole('button', { name: '周一' }));
    expect(container.textContent).not.toContain('隐藏');
    expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放弃' })).toBeInTheDocument();
  });

  it('FormBlock submits current values and handles success', () => {
    const form: FormPart = {
      type: 'form', form_type: 'test', form_id: 'submit', title: '提交测试',
      fields: [{ type: 'text', name: 'name', label: '名称', default_value: 'old' }],
    };
    const submitListener = vi.fn();
    window.addEventListener('chat:form-submit', submitListener);
    render(<FormBlock form={form} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'new' } });
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(submitListener).toHaveBeenCalled();
    const event = submitListener.mock.calls[0][0] as CustomEvent;
    expect(event.detail).toEqual({ formType: 'test', formData: { name: 'new' } });
    expect(screen.getByRole('button', { name: /提交中/ })).toBeDisabled();
    fireEvent(window, new CustomEvent('chat:form-submit-result', { detail: { success: true } }));
    expect(screen.getByText(/已提交/)).toBeInTheDocument();
    window.removeEventListener('chat:form-submit', submitListener);
  });

  it('FormBlock recovers from submit failure and supports cancellation', () => {
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    const form: FormPart = {
      type: 'form', form_type: 'test', form_id: 'failure', title: '失败测试', fields: [],
    };
    const { rerender } = render(<FormBlock form={form} />);
    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    fireEvent(window, new CustomEvent('chat:form-submit-result', {
      detail: { success: false, message: '服务拒绝' },
    }));
    expect(alertMock).toHaveBeenCalledWith('服务拒绝');
    expect(screen.getByRole('button', { name: '确认' })).toBeEnabled();

    rerender(<FormBlock form={{ ...form, form_id: 'cancel' }} />);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.getByText(/已取消/)).toBeInTheDocument();
  });
});
