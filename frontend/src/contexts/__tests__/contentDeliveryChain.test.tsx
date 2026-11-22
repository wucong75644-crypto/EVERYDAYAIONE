import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { create } from 'zustand';
import wire from '../../../../backend/tests/fixtures/content_delivery_wire.json';
import { createStreamingSlice, type StreamingSlice, type StreamingSliceDeps } from '../../stores/slices/streamingSlice';
import { createMessageStreamProjection } from '../messageStreamProjection';
import type { HandlerDeps } from '../wsMessageHandlerShared';
import { calcRemainingText, normalizeMessage } from '../../utils/messageUtils';
import MessageContentBlocks from '../../components/chat/message/MessageContentBlocks';

afterEach(cleanup);
describe('canonical authored delivery through replay, store, rendering and reload', () => {
  it.each([false, true])('displays every prompt once with interleaved thinking=%s and a persisted reload', withThinking => {
    type Store = StreamingSlice & StreamingSliceDeps;
    const store = create<Store>()((set, get, api) => ({ messages: {}, ...createStreamingSlice(set, get, api) }));
    store.getState().startStreaming('conv', 'assistant');
    const projection = createMessageStreamProjection({
      getStore: () => store.getState(), taskConversationMapRef: { current: new Map([['task', 'conv']]) }, send: vi.fn(),
    } as unknown as HandlerDeps);
    const pending = store.getState().optimisticMessages.get('conv')![0];
    const placeholder = render(<MessageContentBlocks message={pending} imageAssets={[]} fileBlocks={[]}
      isStreaming={true} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    expect(screen.getByText('AI 正在处理')).toBeInTheDocument();
    expect(screen.queryByText('AI 正在思考')).not.toBeInTheDocument();
    placeholder.unmount();
    const events = [...wire.events] as Array<{ event_type: string; payload: unknown; delivery_seq: number }>;
    if (withThinking) events.splice(1, 0, { event_type: 'content_block_add',
      payload: { block: { type: 'thinking', text: '核对正文。', duration_ms: 10 } }, delivery_seq: 2 });
    const ordered = events.map((event, index) => ({ ...event, delivery_seq: index + 1 }));
    for (const event of [...ordered, ...ordered]) {
      projection.projectReplayEvent('conv', 'assistant', 'task', 'stream', 1, event);
    }
    const message = store.getState().optimisticMessages.get('conv')![0];
    const texts = message.content.filter(p => p.type === 'text' && p.text);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toMatchObject({ content_artifact_id: 'canonical-artifact' });
    const canonical = wire.events[1].payload.block!.text;
    expect(calcRemainingText(message.content, canonical)).toBe('');
    const view = render(<MessageContentBlocks message={message} imageAssets={[]} fileBlocks={[]}
      isStreaming={true} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    for (const item of wire.proposal.items) {
      expect(screen.getAllByText(item.body.replace(/\s+/g, ' '), { exact: false })).toHaveLength(1);
    }
    const persisted = normalizeMessage({ ...message, status: 'completed', content: JSON.stringify(message.content) });
    view.rerender(<MessageContentBlocks message={persisted} imageAssets={[]} fileBlocks={[]}
      isStreaming={false} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    expect(screen.getAllByText(wire.proposal.display_prefix)).toHaveLength(1);
    for (const item of wire.proposal.items) {
      expect(screen.getAllByText(item.body.replace(/\s+/g, ' '), { exact: false })).toHaveLength(1);
    }
    expect(persisted.content.find(p => p.type === 'text')).toMatchObject({ content_artifact_id: 'canonical-artifact' });
  });
});
