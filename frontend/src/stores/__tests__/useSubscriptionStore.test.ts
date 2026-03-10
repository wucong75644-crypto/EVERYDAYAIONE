/**
 * useSubscriptionStore 单元测试
 *
 * 覆盖：fetchModels/fetchSubscriptions 成功与失败、
 *       subscribe/unsubscribe 正常与异常、
 *       isSubscribed/isSubscribing 查询、clearSubscriptions
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useSubscriptionStore } from '../useSubscriptionStore';

// Mock subscription service
vi.mock('../../services/subscription', () => ({
  getModels: vi.fn(),
  getSubscriptions: vi.fn(),
  subscribeModel: vi.fn(),
  unsubscribeModel: vi.fn(),
}));

// Mock logger
vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

import {
  getModels,
  getSubscriptions,
  subscribeModel,
  unsubscribeModel,
} from '../../services/subscription';

const mockGetModels = vi.mocked(getModels);
const mockGetSubscriptions = vi.mocked(getSubscriptions);
const mockSubscribeModel = vi.mocked(subscribeModel);
const mockUnsubscribeModel = vi.mocked(unsubscribeModel);

function resetStore() {
  useSubscriptionStore.setState({
    subscribedModelIds: [],
    modelInfoMap: {},
    isLoading: false,
    subscribingIds: [],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetStore();
});

// ============================================================
// fetchModels
// ============================================================

describe('fetchModels', () => {
  it('成功加载模型信息到 modelInfoMap', async () => {
    mockGetModels.mockResolvedValue({
      models: [
        { id: 'gemini-3-flash', status: 'active' },
        { id: 'deepseek-v3.2', status: 'active' },
      ],
    });

    await useSubscriptionStore.getState().fetchModels();

    const { modelInfoMap } = useSubscriptionStore.getState();
    expect(Object.keys(modelInfoMap)).toHaveLength(2);
    expect(modelInfoMap['gemini-3-flash'].status).toBe('active');
  });

  it('API 失败不崩溃，modelInfoMap 保持空', async () => {
    mockGetModels.mockRejectedValue(new Error('network error'));

    await useSubscriptionStore.getState().fetchModels();

    const { modelInfoMap } = useSubscriptionStore.getState();
    expect(Object.keys(modelInfoMap)).toHaveLength(0);
  });
});

// ============================================================
// fetchSubscriptions
// ============================================================

describe('fetchSubscriptions', () => {
  it('成功加载订阅列表', async () => {
    mockGetSubscriptions.mockResolvedValue({
      subscriptions: [
        { model_id: 'gemini-3-flash', subscribed_at: '2026-03-10T00:00:00Z' },
        { model_id: 'gemini-3-pro', subscribed_at: '2026-03-10T00:00:00Z' },
      ],
    });

    await useSubscriptionStore.getState().fetchSubscriptions();

    const state = useSubscriptionStore.getState();
    expect(state.subscribedModelIds).toEqual(['gemini-3-flash', 'gemini-3-pro']);
    expect(state.isLoading).toBe(false);
  });

  it('加载期间 isLoading 为 true', async () => {
    let resolvePromise: (value: unknown) => void;
    const pending = new Promise((resolve) => { resolvePromise = resolve; });
    mockGetSubscriptions.mockReturnValue(pending as never);

    const fetchPromise = useSubscriptionStore.getState().fetchSubscriptions();
    expect(useSubscriptionStore.getState().isLoading).toBe(true);

    resolvePromise!({ subscriptions: [] });
    await fetchPromise;
    expect(useSubscriptionStore.getState().isLoading).toBe(false);
  });

  it('API 失败后 isLoading 恢复为 false', async () => {
    mockGetSubscriptions.mockRejectedValue(new Error('fail'));

    await useSubscriptionStore.getState().fetchSubscriptions();

    expect(useSubscriptionStore.getState().isLoading).toBe(false);
    expect(useSubscriptionStore.getState().subscribedModelIds).toEqual([]);
  });
});

// ============================================================
// subscribe
// ============================================================

describe('subscribe', () => {
  it('订阅成功后更新 subscribedModelIds', async () => {
    mockSubscribeModel.mockResolvedValue({
      message: '订阅成功',
      model_id: 'deepseek-v3.2',
    });

    const result = await useSubscriptionStore.getState().subscribe('deepseek-v3.2');

    expect(result).toBe(true);
    expect(useSubscriptionStore.getState().subscribedModelIds).toContain('deepseek-v3.2');
  });

  it('不重复添加已存在的 modelId', async () => {
    useSubscriptionStore.setState({ subscribedModelIds: ['deepseek-v3.2'] });
    mockSubscribeModel.mockResolvedValue({
      message: '订阅成功',
      model_id: 'deepseek-v3.2',
    });

    await useSubscriptionStore.getState().subscribe('deepseek-v3.2');

    const ids = useSubscriptionStore.getState().subscribedModelIds;
    expect(ids.filter((id) => id === 'deepseek-v3.2')).toHaveLength(1);
  });

  it('防止重复点击（subscribingIds 锁定）', async () => {
    useSubscriptionStore.setState({ subscribingIds: ['deepseek-v3.2'] });

    const result = await useSubscriptionStore.getState().subscribe('deepseek-v3.2');

    expect(result).toBe(false);
    expect(mockSubscribeModel).not.toHaveBeenCalled();
  });

  it('订阅期间 isSubscribing 返回 true', async () => {
    let resolvePromise: (value: unknown) => void;
    const pending = new Promise((resolve) => { resolvePromise = resolve; });
    mockSubscribeModel.mockReturnValue(pending as never);

    const subPromise = useSubscriptionStore.getState().subscribe('deepseek-v3.2');
    expect(useSubscriptionStore.getState().isSubscribing('deepseek-v3.2')).toBe(true);

    resolvePromise!({ message: '订阅成功', model_id: 'deepseek-v3.2' });
    await subPromise;
    expect(useSubscriptionStore.getState().isSubscribing('deepseek-v3.2')).toBe(false);
  });

  it('订阅失败后清除 subscribingIds 并抛错', async () => {
    mockSubscribeModel.mockRejectedValue(new Error('fail'));

    await expect(
      useSubscriptionStore.getState().subscribe('deepseek-v3.2'),
    ).rejects.toThrow('fail');

    expect(useSubscriptionStore.getState().isSubscribing('deepseek-v3.2')).toBe(false);
    expect(useSubscriptionStore.getState().subscribedModelIds).not.toContain('deepseek-v3.2');
  });
});

// ============================================================
// unsubscribe
// ============================================================

describe('unsubscribe', () => {
  it('取消订阅后从 subscribedModelIds 移除', async () => {
    useSubscriptionStore.setState({ subscribedModelIds: ['deepseek-v3.2', 'gemini-3-flash'] });
    mockUnsubscribeModel.mockResolvedValue({
      message: '已取消订阅',
      model_id: 'deepseek-v3.2',
    });

    const result = await useSubscriptionStore.getState().unsubscribe('deepseek-v3.2');

    expect(result).toBe(true);
    expect(useSubscriptionStore.getState().subscribedModelIds).toEqual(['gemini-3-flash']);
  });

  it('防止重复操作', async () => {
    useSubscriptionStore.setState({ subscribingIds: ['deepseek-v3.2'] });

    const result = await useSubscriptionStore.getState().unsubscribe('deepseek-v3.2');

    expect(result).toBe(false);
    expect(mockUnsubscribeModel).not.toHaveBeenCalled();
  });

  it('失败后清除 subscribingIds 并抛错', async () => {
    useSubscriptionStore.setState({ subscribedModelIds: ['deepseek-v3.2'] });
    mockUnsubscribeModel.mockRejectedValue(new Error('fail'));

    await expect(
      useSubscriptionStore.getState().unsubscribe('deepseek-v3.2'),
    ).rejects.toThrow('fail');

    expect(useSubscriptionStore.getState().isSubscribing('deepseek-v3.2')).toBe(false);
    // 失败后不应移除
    expect(useSubscriptionStore.getState().subscribedModelIds).toContain('deepseek-v3.2');
  });
});

// ============================================================
// 查询方法
// ============================================================

describe('isSubscribed', () => {
  it('已订阅返回 true', () => {
    useSubscriptionStore.setState({ subscribedModelIds: ['gemini-3-flash'] });
    expect(useSubscriptionStore.getState().isSubscribed('gemini-3-flash')).toBe(true);
  });

  it('未订阅返回 false', () => {
    expect(useSubscriptionStore.getState().isSubscribed('nonexistent')).toBe(false);
  });
});

describe('clearSubscriptions', () => {
  it('清除所有订阅数据', () => {
    useSubscriptionStore.setState({
      subscribedModelIds: ['a', 'b'],
      subscribingIds: ['c'],
    });

    useSubscriptionStore.getState().clearSubscriptions();

    const state = useSubscriptionStore.getState();
    expect(state.subscribedModelIds).toEqual([]);
    expect(state.subscribingIds).toEqual([]);
  });
});
