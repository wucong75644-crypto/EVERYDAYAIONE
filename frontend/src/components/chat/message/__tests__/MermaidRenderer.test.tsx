import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MermaidRenderer from '../MermaidRenderer';

const { renderMock, loggerErrorMock } = vi.hoisted(() => ({
  renderMock: vi.fn(),
  loggerErrorMock: vi.fn(),
}));

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: renderMock,
  },
}));

vi.mock('../../../../utils/logger', () => ({
  logger: { error: loggerErrorMock },
}));

describe('MermaidRenderer', () => {
  beforeEach(() => {
    renderMock.mockReset();
    loggerErrorMock.mockReset();
  });

  afterEach(cleanup);

  it('renders sanitized SVG and removes active content', async () => {
    renderMock.mockResolvedValue({
      svg: '<svg><script>alert(1)</script><foreignObject>bad</foreignObject><text>ok</text></svg>',
    });

    const { container } = render(
      <MermaidRenderer source="flowchart TD\nSafe-->Ready" messageId="message-1" />,
    );

    await screen.findByTestId('mermaid-svg');
    expect(container.querySelector('svg')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('foreignObject')).toBeNull();
    expect(container.textContent).toContain('ok');
  });

  it('shows source fallback and retries after render failure', async () => {
    renderMock
      .mockRejectedValueOnce(new Error('invalid syntax'))
      .mockResolvedValueOnce({ svg: '<svg><text>ready</text></svg>' });

    render(<MermaidRenderer source="invalid diagram source" />);

    expect(await screen.findByText(/关系图渲染失败/)).toBeInTheDocument();
    expect(screen.getByText('invalid diagram source')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新渲染' }));

    await waitFor(() => {
      expect(screen.getByTestId('mermaid-svg')).toHaveTextContent('ready');
    });
    expect(renderMock).toHaveBeenCalledTimes(2);
    expect(loggerErrorMock).toHaveBeenCalledWith(
      'diagram:render',
      'Mermaid render failed',
      undefined,
      expect.objectContaining({
        contentType: 'diagram',
        renderer: 'mermaid',
        errorType: 'Error',
      }),
    );
    expect(JSON.stringify(loggerErrorMock.mock.calls)).not.toContain(
      'invalid diagram source',
    );
  });

  it('does not render Mermaid for empty source', async () => {
    render(<MermaidRenderer source={' \n '} />);

    expect(await screen.findByText('关系图内容为空')).toBeInTheDocument();
    expect(renderMock).not.toHaveBeenCalled();
  });

  it('does not publish an obsolete render after unmount', async () => {
    let resolveRender: ((value: { svg: string }) => void) | undefined;
    renderMock.mockImplementation(() => new Promise((resolve) => {
      resolveRender = resolve;
    }));
    const { unmount } = render(<MermaidRenderer source="flowchart TD\nOld-->Result" />);

    unmount();
    resolveRender?.({ svg: '<svg><text>obsolete</text></svg>' });
    await Promise.resolve();

    expect(screen.queryByText('obsolete')).toBeNull();
  });
});
