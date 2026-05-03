/**
 * workspace 服务单测（searchWorkspace）
 *
 * 覆盖：
 * - 请求参数传递
 * - 响应数据映射
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { searchWorkspace } from '../workspace';

// Mock request 函数
vi.mock('../api', () => ({
  request: vi.fn(),
}));

import { request } from '../api';

const mockRequest = vi.mocked(request);

describe('searchWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should call GET /files/workspace/search with correct params', async () => {
    mockRequest.mockResolvedValue({ items: [], total: 0 });

    await searchWorkspace('report', 10);

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/files/workspace/search',
      params: { q: 'report', limit: 10 },
    });
  });

  it('should use default limit of 20', async () => {
    mockRequest.mockResolvedValue({ items: [], total: 0 });

    await searchWorkspace('test');

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/files/workspace/search',
      params: { q: 'test', limit: 20 },
    });
  });

  it('should return response data', async () => {
    const mockItems = [
      { name: 'file.txt', is_dir: false, size: 100, modified: '123', cdn_url: null, mime_type: 'text/plain', workspace_path: 'file.txt' },
    ];
    mockRequest.mockResolvedValue({ items: mockItems, total: 1 });

    const result = await searchWorkspace('file');

    expect(result.items).toHaveLength(1);
    expect(result.items[0].name).toBe('file.txt');
    expect(result.total).toBe(1);
  });
});
