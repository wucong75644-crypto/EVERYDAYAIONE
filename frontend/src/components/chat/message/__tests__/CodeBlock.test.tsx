import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CodeBlock from '../CodeBlock';

describe('CodeBlock', () => {
  const writeText = vi.fn<() => Promise<void>>();

  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
  });

  it('copies the original code instead of highlighted nodes', async () => {
    const rawCode = '{"name":"test","items":[{"id":1}]}';
    render(<CodeBlock language="json" rawCode={rawCode} />);

    fireEvent.click(screen.getByTitle('复制代码'));

    expect(writeText).toHaveBeenCalledWith(rawCode);
    await waitFor(() => expect(screen.getByTitle('已复制')).toBeInTheDocument());
  });

  it('renders unknown languages without losing source text', () => {
    const rawCode = 'custom <value> & data';
    const { container } = render(<CodeBlock language="unknown-lang" rawCode={rawCode} />);

    expect(container.textContent).toContain(rawCode);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('keeps the copy action idle when clipboard permission is denied', async () => {
    writeText.mockRejectedValueOnce(new Error('denied'));
    render(<CodeBlock rawCode="plain text" />);

    fireEvent.click(screen.getByTitle('复制代码'));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('plain text'));
    expect(screen.getByTitle('复制代码')).toBeInTheDocument();
  });
});
