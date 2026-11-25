import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { create } from 'zustand';
import wire from '../../../../backend/tests/fixtures/content_delivery_wire.json';
import realFailure from '../../../../backend/tests/fixtures/chat_skill_failures_20261122.json';
import realSupplier from '../../../../backend/tests/fixtures/chat_skill_real_supplier_delivery_20261122.json';
import formalDelivery from '../../../../backend/tests/fixtures/chat_skill_formal_delivery_20261123.json';
import image2Delivery from '../../../../backend/tests/fixtures/chat_skill_image2_delivery_20261123.json';
import { createStreamingSlice, type StreamingSlice, type StreamingSliceDeps } from '../../stores/slices/streamingSlice';
import { createMessageStreamProjection } from '../messageStreamProjection';
import type { HandlerDeps } from '../wsMessageHandlerShared';
import { calcRemainingText, normalizeMessage } from '../../utils/messageUtils';
import MessageContentBlocks from '../../components/chat/message/MessageContentBlocks';

afterEach(cleanup);

it('keeps commentary separate from the registered original through replay and reload', async () => {
  type Store = StreamingSlice & StreamingSliceDeps;
  const store = create<Store>()((set, get, api) => ({ messages: {}, ...createStreamingSlice(set, get, api) }));
  store.getState().startStreaming('conv', 'assistant');
  const projection = createMessageStreamProjection({
    getStore: () => store.getState(), taskConversationMapRef: { current: new Map([['task', 'conv']]) }, send: vi.fn(),
  } as unknown as HandlerDeps);
  const intro = '我看到了参考图，下面给你完整方案。';
  const body = '## 1. 浅蓝方案\n\n保留封面波点，背景浅蓝。\n禁止改变原有文字。';
  const events = [{ type: 'text', text: intro },
    { type: 'text', text: body, content_artifact_id: 'registered-original', content_delivery_final: true }]
    .map((block, index) => ({ event_type: 'content_block_add', payload: { block }, delivery_seq: index + 1 }));
  for (const event of [...events, ...events]) projection.projectReplayEvent('conv', 'assistant', 'task', 'stream', 1, event);
  const message = store.getState().optimisticMessages.get('conv')![0];
  expect(message.content.filter(part => part.type === 'text')).toEqual([
    expect.objectContaining({ text: intro }),
    expect.objectContaining({ text: body, content_artifact_id: 'registered-original' }),
  ]);
  const persisted = normalizeMessage({ ...message, status: 'completed', content: JSON.stringify(message.content) });
  const view = await act(async () => render(<MessageContentBlocks message={message} imageAssets={[]} fileBlocks={[]}
    isStreaming={true} isRegenerating={false} textContent="" onImageClick={vi.fn()} />));
  expect(screen.getAllByText(intro)).toHaveLength(1);
  expect(screen.getAllByRole('heading', { name: '1. 浅蓝方案' })).toHaveLength(1);
  await act(async () => view.rerender(<MessageContentBlocks message={persisted} imageAssets={[]} fileBlocks={[]}
    isStreaming={false} isRegenerating={false} textContent="" onImageClick={vi.fn()} />));
  expect(screen.getAllByText(intro)).toHaveLength(1);
  expect(screen.getAllByRole('heading', { name: '1. 浅蓝方案' })).toHaveLength(1);
  expect(persisted.content.find(part => part.type === 'text' && part.content_artifact_id))
    .toMatchObject({ text: body, content_artifact_id: 'registered-original' });
  expect(persisted.content.find(part => part.type === 'text' && part.text === intro))
    .not.toHaveProperty('content_artifact_id');
});

it.each([formalDelivery, image2Delivery])('replays a real formal-entry plan and its whole body once through render and reload ($provenance.source)', async delivery => {
  const planApi = await import('../../services/ecommerceImagePlans');
  const getPlan = vi.spyOn(planApi, 'getEcommerceImagePlan').mockResolvedValue({
    id: delivery.plan.plan_id, revision: delivery.plan.revision,
    status: 'ready', image_count: 3, images: [],
  });
  try {
    type Store = StreamingSlice & StreamingSliceDeps;
    const store = create<Store>()((set, get, api) => ({ messages: {}, ...createStreamingSlice(set, get, api) }));
    store.getState().startStreaming('conv', 'assistant');
    const projection = createMessageStreamProjection({
      getStore: () => store.getState(), taskConversationMapRef: { current: new Map([['task', 'conv']]) }, send: vi.fn(),
    } as unknown as HandlerDeps);
    for (const event of [...delivery.events, ...delivery.events]) {
      projection.projectReplayEvent('conv', 'assistant', 'task', 'stream', 1, event);
    }
    const message = store.getState().optimisticMessages.get('conv')![0];
    expect(message.content.filter(part => part.type === 'text')).toHaveLength(1);
    expect(message.content.find(part => part.type === 'text')).toMatchObject({ text: delivery.canonical_text });
    const persisted = normalizeMessage({ ...message, status: 'completed', content: JSON.stringify(message.content) });
    const view = await act(async () => render(<MessageContentBlocks message={message} imageAssets={[]} fileBlocks={[]}
      isStreaming={true} isRegenerating={false} textContent="" onImageClick={vi.fn()} />));
    for (const image of delivery.plan.images) {
      expect(screen.getAllByRole('heading', { name: `${image.position}. ${image.name}` })).toHaveLength(1);
    }
    await act(async () => view.rerender(<MessageContentBlocks message={persisted} imageAssets={[]} fileBlocks={[]}
      isStreaming={false} isRegenerating={false} textContent="" onImageClick={vi.fn()} />));
    expect(persisted.content.find(part => part.type === 'text')).toMatchObject({ text: delivery.canonical_text });
    expect(persisted.content.find(part => part.type === 'ecom_plan')).toMatchObject(delivery.plan);
    expect(calcRemainingText(persisted.content, delivery.canonical_text)).toBe('');
    for (const image of delivery.plan.images) {
      expect(screen.getAllByRole('heading', { name: `${image.position}. ${image.name}` })).toHaveLength(1);
    }
  } finally { getPlan.mockRestore(); }
});

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


describe('real failed five-plan reply through the delivery and render chain', () => {
  it('retains all five bodies once after duplicate replay and a persisted reload', () => {
    type Store = StreamingSlice & StreamingSliceDeps;
    const store = create<Store>()((set, get, api) => ({ messages: {}, ...createStreamingSlice(set, get, api) }));
    store.getState().startStreaming('conv', 'assistant');
    const projection = createMessageStreamProjection({
      getStore: () => store.getState(), taskConversationMapRef: { current: new Map([['task', 'conv']]) }, send: vi.fn(),
    } as unknown as HandlerDeps);
    for (const event of [...realFailure.wire_events, ...realFailure.wire_events]) {
      projection.projectReplayEvent('conv', 'assistant', 'task', 'stream', 1, event);
    }
    const message = store.getState().optimisticMessages.get('conv')![0];
    const texts = message.content.filter(part => part.type === 'text' && part.text);
    expect(texts).toHaveLength(1);
    expect(texts[0]).toMatchObject({ text: realFailure.plan.response, content_artifact_id: 'failure-canonical-artifact' });
    const view = render(<MessageContentBlocks message={message} imageAssets={[]} fileBlocks={[]}
      isStreaming={true} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    for (const item of realFailure.plan.expected_items) {
      expect(screen.getAllByText(item.body, { exact: false })).toHaveLength(1);
    }
    const persisted = normalizeMessage({ ...message, status: 'completed', content: JSON.stringify(message.content) });
    view.rerender(<MessageContentBlocks message={persisted} imageAssets={[]} fileBlocks={[]}
      isStreaming={false} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    for (const item of realFailure.plan.expected_items) {
      expect(screen.getAllByText(item.body, { exact: false })).toHaveLength(1);
    }
    expect(persisted.content.find(part => part.type === 'text')).toMatchObject({
      content_artifact_id: 'failure-canonical-artifact', text: realFailure.plan.response,
    });
    expect(calcRemainingText(persisted.content, realFailure.plan.response)).toBe('');
  });
});


describe('real supplier image display and persisted reload', () => {
  it.each([...realSupplier.cases, ...formalDelivery.image_cases, ...image2Delivery.image_cases])('renders $label with the actual file dimensions and retained asset', sample => {
    const raw = sample.done.payload.message;
    const message = normalizeMessage({ ...raw, content: JSON.stringify(raw.content) } as Parameters<typeof normalizeMessage>[0]);
    expect(message.content).toHaveLength(1);
    expect(message.content[0]).toMatchObject({ type: 'image', width: sample.actual_width, height: sample.actual_height,
      url: sample.expected_url, asset_id: expect.any(String), quality_checks: { size_matches: true } });
    expect(message.content[0]).toMatchObject({ quality_checks: raw.content[0].quality_checks });
    const click = vi.fn();
    const view = render(<MessageContentBlocks message={message} imageAssets={[{ originalUrl: sample.expected_url }]}
      fileBlocks={[]} isStreaming={false} isRegenerating={false} textContent="" onImageClick={click} />);
    const image = screen.getByRole('img', { hidden: true });
    expect(image).toHaveAttribute('src', sample.expected_url);
    fireEvent.load(image);
    fireEvent.click(screen.getByRole('img'));
    expect(click).toHaveBeenCalledWith(0);
    const reloaded = normalizeMessage({ ...message, content: JSON.stringify(message.content) });
    view.rerender(<MessageContentBlocks message={reloaded} imageAssets={[{ originalUrl: sample.expected_url }]}
      fileBlocks={[]} isStreaming={false} isRegenerating={false} textContent="" onImageClick={click} />);
    expect(screen.getAllByRole('img', { hidden: true })).toHaveLength(1);
    expect(reloaded.content[0]).toEqual(message.content[0]);
  });
});

it('replays native ready plan text once and preserves its saved number references after reload', async () => {
  const planApi = await import('../../services/ecommerceImagePlans');
  const getPlan = vi.spyOn(planApi, 'getEcommerceImagePlan').mockResolvedValue({
    id: 'native-saved-plan', revision: 1, status: 'ready', image_count: 2, images: [],
  });
  try {
    type Store = StreamingSlice & StreamingSliceDeps;
    const store = create<Store>()((set, get, api) => ({ messages: {}, ...createStreamingSlice(set, get, api) }));
    store.getState().startStreaming('conv', 'assistant');
    const projection = createMessageStreamProjection({
      getStore: () => store.getState(), taskConversationMapRef: { current: new Map([['task', 'conv']]) }, send: vi.fn(),
    } as unknown as HandlerDeps);
    const plan = { type: 'ecom_plan', plan_id: 'native-saved-plan', revision: 1, status: 'ready',
      product_insight: 'A5卡册', visual_strategy: '保留形态，只改背景', images: [
        { item_id: 'first-item', position: 1, name: '浅蓝方案', purpose: '展示封面' },
        { item_id: 'second-item', position: 2, name: '米灰方案', purpose: '展示细节' },
      ] };
    const text = '## 1. 浅蓝方案\n\n保留波点，背景浅蓝。\r\n禁止改变商标。\n\n## 2. 米灰方案\n\n保留装订，背景米灰。';
    const events = [plan, { type: 'text', text }].map((block, index) => ({
      event_type: 'content_block_add', payload: { block }, delivery_seq: index + 1,
    }));
    for (const event of [...events, ...events]) projection.projectReplayEvent('conv', 'assistant', 'task', 'stream', 1, event);
    const streamed = store.getState().optimisticMessages.get('conv')![0];
    expect(streamed.content).toHaveLength(2);
    expect(streamed.content.find(part => part.type === 'ecom_plan')).toMatchObject(plan);
    expect(streamed.content.find(part => part.type === 'text')).toMatchObject({ text });
    const reloaded = normalizeMessage({ ...streamed, status: 'completed', content: JSON.stringify(streamed.content) });
    await act(async () => {
      render(<MessageContentBlocks message={reloaded} imageAssets={[]} fileBlocks={[]}
        isStreaming={false} isRegenerating={false} textContent="" onImageClick={vi.fn()} />);
    });
    expect(screen.getAllByText('浅蓝方案', { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByText('禁止改变商标。', { exact: false })).toHaveLength(1);
    expect(reloaded.content.find(part => part.type === 'ecom_plan')).toMatchObject({
      plan_id: 'native-saved-plan', revision: 1, images: [
        { item_id: 'first-item', position: 1 }, { item_id: 'second-item', position: 2 },
      ],
    });
    expect(calcRemainingText(reloaded.content, text)).toBe('');
    expect(screen.queryByRole('button', { name: /确认.*生成/ })).not.toBeInTheDocument();
  } finally { getPlan.mockRestore(); }
});
