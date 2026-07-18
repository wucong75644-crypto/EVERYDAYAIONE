import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DiagramBlock from '../DiagramBlock';

vi.mock('../MermaidRenderer', () => ({
  default: ({ source }: { source: string }) => (
    <div data-testid="renderer-source">{source}</div>
  ),
}));

describe('DiagramBlock', () => {
  it('passes original source to renderer and copy action', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    const source = 'flowchart TD\nA-->B';

    render(
      <DiagramBlock
        messageId="message-1"
        diagram={{ type: 'diagram', format: 'mermaid', title: '流程', source }}
      />,
    );

    expect(screen.getByTestId('renderer-source').textContent).toBe(source);
    fireEvent.click(screen.getByRole('button', { name: '复制源码' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(source));
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument();
  });
});
