/**
 * services/message.ts API 函数测试
 *
 * 重点验证 V3 升级后新增的 searchMessages 和 getMessages 的 cursor 支持。
 *
 * Mock 策略：mock api.request，验证 URL / params / 错误处理。
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getMessages, searchMessages } from '../message';
import { request } from '../api';

vi.mock('../api', () => ({
  request: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('getMessages — cursor 分页支持', () => {
  it('传 beforeId 时正确放进 params.before_id', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      has_more: false,
    });

    await getMessages('conv-1', 30, 0, 'msg-cursor-id');

    expect(request).toHaveBeenCalledWith({
      url: '/conversations/conv-1/messages',
      method: 'GET',
      params: { limit: 30, offset: 0, before_id: 'msg-cursor-id' },
      signal: undefined,
    });
  });

  it('不传 beforeId 时 params.before_id 为 undefined（兼容首次加载）', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      has_more: false,
    });

    await getMessages('conv-1', 30, 0);

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        params: expect.objectContaining({ before_id: undefined }),
      }),
    );
  });

  it('支持 AbortSignal 透传', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      has_more: false,
    });

    const controller = new AbortController();
    await getMessages('conv-1', 30, 0, 'cursor', controller.signal);

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});

describe('searchMessages — 搜索 API', () => {
  it('调用正确的搜索 URL 和 query 参数', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      query: '关键词',
    });

    await searchMessages('conv-abc', '关键词');

    expect(request).toHaveBeenCalledWith({
      url: '/conversations/conv-abc/messages/search',
      method: 'GET',
      params: { q: '关键词', limit: 20 },
      signal: undefined,
    });
  });

  it('自定义 limit 透传', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      query: 'x',
    });

    await searchMessages('conv-abc', 'x', 50);

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        params: { q: 'x', limit: 50 },
      }),
    );
  });

  it('返回结果保留 query 字段供前端高亮', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [{ id: 'm1' }, { id: 'm2' }],
      total: 2,
      query: '订单',
    });

    const result = await searchMessages('conv', '订单');

    expect(result.query).toBe('订单');
    expect(result.total).toBe(2);
    expect(result.messages).toHaveLength(2);
  });

  it('支持 AbortSignal 用于取消旧搜索', async () => {
    vi.mocked(request).mockResolvedValue({
      messages: [],
      total: 0,
      query: 'x',
    });

    const controller = new AbortController();
    await searchMessages('conv', 'x', 20, controller.signal);

    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});
