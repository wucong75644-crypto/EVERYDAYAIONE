import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MessageBubbleContent from '../MessageBubbleContent';
import { getTextContent, useMessageStore, type Message, type ToolStepPart } from '../../../../stores/useMessageStore';
import { streamingTextDisplayBaseline } from '../../../../utils/streamingTextDisplay';

const text = '连续输出的正文'.repeat(40);
const delta = '后续实时增量'.repeat(30);
const tool: ToolStepPart = { type: 'tool_step', tool_name: 'test', tool_call_id: 'tool-1', status: 'completed' };
const current = () => useMessageStore.getState().optimisticMessages.get('conversation')![0];
const store = () => useMessageStore.getState();

function Bubble({ message }: { message: Message }) {
  return <MessageBubbleContent message={message} isUser={false}
    hasMultiBlocks={message.content.some((part) => part.type === 'tool_step' || part.type === 'tool_result')}
    imageAssets={[]} fileBlocks={[]} isStreaming={message.status === 'streaming'} isRegenerating={false}
    textContent={getTextContent(message)} hasImage={false} hasVideo={false} hasFiles={false}
    isErrorMessage={message.status === 'failed'} onImageClick={vi.fn()} />;
}

describe('actual message streaming presentation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now()), 16)));
    vi.stubGlobal('cancelAnimationFrame', clearTimeout);
    useMessageStore.setState({ messages: {}, optimisticMessages: new Map(), streamingMessages: new Map() });
    store().startStreaming('conversation', 'message');
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it.each(['plain', 'markdown', 'code', 'table'])('paces the first live %s chunk through the loading bubble', (format) => {
    const content = format === 'markdown' ? `# 标题\n\n${text}`
      : format === 'code' ? `\`\`\`js\n${'const value = 1;\n'.repeat(30)}\`\`\``
      : format === 'table' ? `| 字段 | 数值 |\n|---|---|\n${'| 项目 | 内容 |\n'.repeat(25)}` : text;
    const view = render(<Bubble message={current()} />);
    expect(view.container.textContent).toContain('AI 正在思考');
    act(() => store().appendStreamingContent('conversation', content));
    view.rerender(<Bubble message={current()} />);
    expect(requestAnimationFrame).toHaveBeenCalled();
    if (format === 'plain') expect(view.container.textContent).not.toContain(text);
    if (format === 'table') expect(view.container.querySelectorAll('tbody tr').length).toBeLessThan(25);
    act(() => vi.advanceTimersByTime(128));
    if (format === 'plain') expect(view.container.textContent).toContain(text);
    if (format === 'code') expect(view.container.querySelector('code')?.textContent).toBe('const value = 1;\n'.repeat(30).trimEnd());
    if (format === 'table') expect(view.container.querySelectorAll('tbody tr')).toHaveLength(25);
  });

  it('shows a restored snapshot immediately and paces subsequent live text', () => {
    const view = render(<Bubble message={current()} />);
    act(() => store().setStreamingContent('conversation', text));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text);
    expect(requestAnimationFrame).not.toHaveBeenCalled();
    expect(current().content).toEqual([{ type: 'text', text }]);
    act(() => store().appendStreamingContent('conversation', delta));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text);
    expect(view.container.textContent).not.toContain(text + delta);
    act(() => vi.advanceTimersByTime(128));
    expect(view.container.textContent).toContain(text + delta);
  });

  it('preserves a snapshot baseline when restore and live delta share one render', () => {
    const view = render(<Bubble message={current()} />);
    act(() => {
      store().setStreamingContent('conversation', text);
      store().appendStreamingContent('conversation', delta);
    });
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text);
    expect(view.container.textContent).not.toContain(text + delta);
    expect(streamingTextDisplayBaseline(current().content)?.text).toBe(text);
    act(() => vi.advanceTimersByTime(128));
    expect(view.container.textContent).toContain(text + delta);
  });

  it('flushes a prefix-preserving snapshot over an active animation immediately', () => {
    const view = render(<Bubble message={current()} />);
    act(() => store().appendStreamingContent('conversation', text));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).not.toContain(text);
    act(() => store().setStreamingContent('conversation', text + delta));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text + delta);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([false, true])('does not replay already populated history on mount, structured=%s', (structured) => {
    if (structured) store().restoreStreamingBlocks('conversation', [tool, { type: 'text', text }], '');
    else store().appendStreamingContent('conversation', text);
    const view = render(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text);
    expect(requestAnimationFrame).not.toHaveBeenCalled();
  });

  it('paces new text after an observed tool step', () => {
    store().appendContentBlock('conversation', tool);
    const view = render(<Bubble message={current()} />);
    act(() => store().appendStreamingContent('conversation', text));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).not.toContain(text);
    expect(requestAnimationFrame).toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(128));
    expect(view.container.textContent).toContain(text);
  });

  it.each([false, true])('paces a new segment when tool and text arrive in one render, previousStructured=%s', (structured) => {
    if (structured) store().appendContentBlock('conversation', { ...tool, tool_call_id: 'earlier-tool' });
    store().appendStreamingContent('conversation', '之前已展示的正文。');
    const view = render(<Bubble message={current()} />);
    act(() => {
      store().appendContentBlock('conversation', tool);
      store().appendStreamingContent('conversation', text);
    });
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain('之前已展示的正文。');
    expect(view.container.textContent).not.toContain(text);
    expect(requestAnimationFrame).toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(128));
    expect(view.container.textContent).toContain(text);
    view.rerender(<Bubble message={{ ...current(), status: 'completed' }} />);
    const rendered = view.container.textContent!;
    expect(rendered.indexOf('之前已展示的正文。')).toBeLessThan(rendered.indexOf(text));
    expect(rendered.split(text)).toHaveLength(2);
  });

  it('restores structured text and paces a following delta in the same batch', () => {
    store().appendContentBlock('conversation', tool);
    const view = render(<Bubble message={current()} />);
    act(() => {
      store().restoreStreamingBlocks('conversation', [tool], text);
      store().updateContentBlock('conversation', tool.tool_call_id, { output: '完成' });
      store().appendStreamingContent('conversation', delta);
    });
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).toContain(text);
    expect(view.container.textContent).not.toContain(text + delta);
    act(() => vi.advanceTimersByTime(128));
    expect(view.container.textContent).toContain(text + delta);
  });

  it.each(['completed', 'interrupted', 'failed'] as const)('reveals all text immediately on %s', (status) => {
    const view = render(<Bubble message={current()} />);
    act(() => store().appendStreamingContent('conversation', text));
    view.rerender(<Bubble message={current()} />);
    expect(view.container.textContent).not.toContain(text);
    view.rerender(<Bubble message={{ ...current(), status }} />);
    expect(view.container.textContent).toContain(text);
    expect(vi.getTimerCount()).toBe(0);
  });
});
