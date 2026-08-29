import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Message } from '../../../../types/message';
import MessageContentBlocks from '../MessageContentBlocks';

vi.mock('../DiagramBlock', () => ({
  default: ({
    diagram,
    messageId,
  }: {
    diagram: { source: string };
    messageId: string;
  }) => (
    <div data-testid="diagram-dispatch">
      {messageId}:{diagram.source}
    </div>
  ),
}));

describe('MessageContentBlocks structured diagrams', () => {
  it('dispatches a diagram part through the dedicated structured renderer', async () => {
    const message: Message = {
      id: 'message-1',
      conversation_id: 'conversation-1',
      role: 'assistant',
      status: 'completed',
      created_at: '2026-07-18T00:00:00Z',
      content: [{
        type: 'diagram',
        format: 'mermaid',
        title: '订单流程',
        source: 'flowchart TD\nA-->B',
      }],
    };

    render(
      <MessageContentBlocks
        message={message}
        imageAssets={[]}
        fileBlocks={[]}
        isStreaming={false}
        isRegenerating={false}
        textContent=""
        onImageClick={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('diagram-dispatch')).toHaveTextContent(
      'message-1:flowchart TD A-->B',
    );
  });

  it('keeps the structured scheduled-task form but hides legacy duplicate confirmation copy', () => {
    const message: Message = {
      id: 'message-2',
      conversation_id: 'conversation-1',
      role: 'assistant',
      status: 'completed',
      created_at: '2026-07-18T00:00:00Z',
      content: [
        { type: 'text', text: '我来帮你创建每日付款订单汇报。' },
        {
          type: 'form',
          form_type: 'scheduled_task_confirm',
          form_id: 'confirm-1',
          title: '确认启用定时任务',
          fields: [],
        },
        {
          type: 'text',
          text: '配置表单已生成，任务尚未创建。\n\n任务预览：每天早上 8:30 汇总。\n\n请确认是否创建此定时任务？',
        },
      ],
    };

    render(
      <MessageContentBlocks
        message={message}
        imageAssets={[]}
        fileBlocks={[]}
        isStreaming={false}
        isRegenerating={false}
        textContent=""
        onImageClick={vi.fn()}
      />,
    );

    expect(screen.getByText('我来帮你创建每日付款订单汇报。')).toBeInTheDocument();
    expect(screen.getByText('确认启用定时任务')).toBeInTheDocument();
    expect(screen.queryByText(/配置表单已生成/)).not.toBeInTheDocument();
  });
});
