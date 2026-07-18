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
});
