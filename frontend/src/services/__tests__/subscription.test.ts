/**
 * subscription API 服务单元测试
 *
 * 覆盖：getModels / getSubscriptions / subscribeModel / unsubscribeModel
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock api module
vi.mock('../api', () => ({
  request: vi.fn(),
}));

import { request } from '../api';
import {
  getModels,
  getSubscriptions,
  subscribeModel,
  unsubscribeModel,
} from '../subscription';

const mockRequest = vi.mocked(request);

beforeEach(() => {
  vi.clearAllMocks();
});

// ============================================================
// getModels
// ============================================================

describe('getModels', () => {
  it('调用 GET /models', async () => {
    const mockResponse = {
      models: [
        { id: 'gemini-3-flash', status: 'active' },
        { id: 'deepseek-v3.2', status: 'active' },
      ],
    };
    mockRequest.mockResolvedValue(mockResponse);

    const result = await getModels();

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/models',
    });
    expect(result.models).toHaveLength(2);
  });

  it('API 失败时抛出异常', async () => {
    mockRequest.mockRejectedValue(new Error('network error'));

    await expect(getModels()).rejects.toThrow('network error');
  });
});

// ============================================================
// getSubscriptions
// ============================================================

describe('getSubscriptions', () => {
  it('调用 GET /subscriptions', async () => {
    const mockResponse = {
      subscriptions: [
        { model_id: 'gemini-3-flash', subscribed_at: '2026-03-10T00:00:00Z' },
      ],
    };
    mockRequest.mockResolvedValue(mockResponse);

    const result = await getSubscriptions();

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/subscriptions',
    });
    expect(result.subscriptions).toHaveLength(1);
  });
});

// ============================================================
// subscribeModel
// ============================================================

describe('subscribeModel', () => {
  it('调用 POST /subscriptions/{modelId}', async () => {
    const mockResponse = { message: '订阅成功', model_id: 'gemini-3-flash' };
    mockRequest.mockResolvedValue(mockResponse);

    const result = await subscribeModel('gemini-3-flash');

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'POST',
      url: '/subscriptions/gemini-3-flash',
    });
    expect(result.message).toBe('订阅成功');
  });

  it('对含斜杠的模型 ID 进行 URL 编码', async () => {
    mockRequest.mockResolvedValue({ message: '订阅成功', model_id: 'openai/gpt-5.4' });

    await subscribeModel('openai/gpt-5.4');

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'POST',
      url: '/subscriptions/openai%2Fgpt-5.4',
    });
  });

  it('API 失败时抛出异常', async () => {
    mockRequest.mockRejectedValue(new Error('未知的模型'));

    await expect(subscribeModel('bad-model')).rejects.toThrow('未知的模型');
  });
});

// ============================================================
// unsubscribeModel
// ============================================================

describe('unsubscribeModel', () => {
  it('调用 DELETE /subscriptions/{modelId}', async () => {
    const mockResponse = { message: '已取消订阅', model_id: 'deepseek-r1' };
    mockRequest.mockResolvedValue(mockResponse);

    const result = await unsubscribeModel('deepseek-r1');

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'DELETE',
      url: '/subscriptions/deepseek-r1',
    });
    expect(result.message).toBe('已取消订阅');
  });

  it('对含斜杠的模型 ID 进行 URL 编码', async () => {
    mockRequest.mockResolvedValue({ message: '已取消订阅', model_id: 'openai/gpt-5.4' });

    await unsubscribeModel('openai/gpt-5.4');

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'DELETE',
      url: '/subscriptions/openai%2Fgpt-5.4',
    });
  });
});
