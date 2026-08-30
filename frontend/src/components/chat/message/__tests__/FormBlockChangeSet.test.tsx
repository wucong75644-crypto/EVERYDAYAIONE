import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FormPart } from '../../../../types/message';
import FormBlock from '../FormBlock';

vi.mock('../ChangeSetCard', () => ({
  default: ({ changeSetId }: { changeSetId: string }) => <div data-testid="changeset-card">ChangeSet:{changeSetId}</div>,
}));

describe('FormBlock ChangeSet association', () => {
  it('keeps form input separate from the ChangeSet display projection', () => {
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

    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ detail: expect.objectContaining({
      formData: { name: '新草案' }, changeSetId: 'change-1',
    }) }));
    window.removeEventListener('chat:form-submit', listener);
  });
});
