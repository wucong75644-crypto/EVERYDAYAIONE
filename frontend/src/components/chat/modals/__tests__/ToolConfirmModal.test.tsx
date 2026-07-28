import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ToolConfirmModal from '../ToolConfirmModal';

describe('ToolConfirmModal', () => {
  it('renders nothing without an active request', () => {
    const { container } = render(
      <ToolConfirmModal request={null} onConfirm={vi.fn()} onReject={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders only the server-provided redacted summary', () => {
    render(
      <ToolConfirmModal
        request={{
          confirmationId: 'confirmation-1',
          toolName: 'erp_execute',
          timeout: 60,
          confirmationSummary: { description: '执行ERP业务操作', operation: 'update' },
        }}
        onConfirm={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByText('执行ERP业务操作')).toBeInTheDocument();
    expect(screen.getByText('update')).toBeInTheDocument();
  });

  it('invokes confirm, reject and automatic timeout actions', () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    const onReject = vi.fn();
    const request = {
      confirmationId: 'confirmation-2', toolName: 'custom_tool',
      confirmationSummary: {}, timeout: 2,
    };
    const { rerender } = render(
      <ToolConfirmModal request={request} onConfirm={onConfirm} onReject={onReject} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    expect(onConfirm).toHaveBeenCalledWith('confirmation-2');
    fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(onReject).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(2000));
    expect(onReject).not.toHaveBeenCalled();
    rerender(<ToolConfirmModal request={null} onConfirm={onConfirm} onReject={onReject} />);
    expect(screen.queryByText('写操作确认')).toBeNull();
    vi.useRealTimers();
  });
});
