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

  it('renders structured and circular arguments safely', () => {
    const circular: { self?: unknown } = {};
    circular.self = circular;

    render(
      <ToolConfirmModal
        request={{
          toolCallId: 'call-1',
          toolName: 'erp_execute',
          description: '确认更新',
          timeout: 60,
          arguments: { payload: { answer: 42 }, circular },
        }}
        onConfirm={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByText('{"answer":42}')).toBeInTheDocument();
    expect(screen.getByText('[无法显示的结构化数据]')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('invokes confirm, reject and automatic timeout actions', () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    const onReject = vi.fn();
    const request = {
      toolCallId: 'call-2', toolName: 'custom_tool', arguments: {},
      description: '', timeout: 2,
    };
    const { rerender } = render(
      <ToolConfirmModal request={request} onConfirm={onConfirm} onReject={onReject} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    expect(onConfirm).toHaveBeenCalledWith('call-2');
    fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(onReject).toHaveBeenCalledWith('call-2');

    act(() => vi.advanceTimersByTime(2000));
    expect(onReject).toHaveBeenCalledWith('call-2');
    rerender(<ToolConfirmModal request={null} onConfirm={onConfirm} onReject={onReject} />);
    expect(screen.queryByText('写操作确认')).toBeNull();
    vi.useRealTimers();
  });
});
