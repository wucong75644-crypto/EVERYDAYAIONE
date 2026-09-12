import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FormPart } from '../../../../types/message';
import FormBlock from '../FormBlock';

vi.mock('../ChangeSetCard', () => ({
  default: ({ changeSetId }: { changeSetId: string }) => <div data-testid="changeset-card">ChangeSet:{changeSetId}</div>,
}));

describe('FormBlock ChangeSet association', () => {
  it('keeps form input separate from the server-owned ChangeSet reference', () => {
    const form: FormPart = {
      type: 'form', form_type: 'scheduled_task_create', form_id: 'form-1',
      title: '配置任务', change_set_id: 'change-1',
      fields: [{ type: 'text', name: 'name', label: '名称', default_value: '草案' }],
    };
    const listener = vi.fn();
    window.addEventListener('chat:form-submit', listener);
    render(<FormBlock form={form} messageId="message-1" conversationId="conversation-1" />);

    expect(screen.getByTestId('changeset-card')).toHaveTextContent('ChangeSet:change-1');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '新草案' } });
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    const submitted = listener.mock.calls[0]?.[0] as CustomEvent;
    expect(submitted.detail).toMatchObject({
      formData: { name: '新草案' },
      formId: 'form-1',
      messageId: 'message-1',
      conversationId: 'conversation-1',
    });
    expect(submitted.detail).not.toHaveProperty('changeSetId');
    window.removeEventListener('chat:form-submit', listener);
  });
  it('uses the live card instead of the initial checking receipt after submission', () => {
    render(<FormBlock form={{
      type: 'form', form_type: 'scheduled_task_create', form_id: 'form-2', title: '创建任务',
      fields: [], status: 'submitted', change_set_id: 'change-2',
      result_message: '正在检查创建请求，结果将在卡片中更新。',
    }} messageId="m2" conversationId="c2" />);
    expect(screen.getByTestId('changeset-card')).toHaveTextContent('change-2');
    expect(screen.queryByText(/正在检查创建请求/)).not.toBeInTheDocument();
  });

});

it('submits the backend structured patch with its resolved ID and boolean marker', async () => {
  const { default: fixture } = await import('../../../../../../backend/tests/fixtures/scheduled_task_structured_form.json');
  const listener = vi.fn();
  window.addEventListener('chat:form-submit', listener);
  try {
    render(<FormBlock form={fixture as FormPart} messageId="m-patch" conversationId="c-patch" />);
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    expect(listener).not.toHaveBeenCalled();
    expect(screen.getByText('请选择每周几')).toBeVisible();
    fireEvent.click(screen.getByText('周一'));
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    const event = listener.mock.calls[0][0] as CustomEvent;
    expect(event.detail.formData).toEqual({
      task_id: 'task-a', schedule_type: 'weekly', weekdays: [1],
      _submission_mode: 'apply_if_allowed', _structured_input: true,
    });
  } finally {
    window.removeEventListener('chat:form-submit', listener);
  }
});
