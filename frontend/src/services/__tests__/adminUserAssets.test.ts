import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  API_BASE_URL: '/api',
  request: vi.fn(),
}));

import { request } from '../api';
import { downloadUserAssetsZip, listUserAssets } from '../adminUser';

const mockRequest = vi.mocked(request);

describe('admin user assets service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('passes filters, opaque cursor, and abort signal to the unified list endpoint', async () => {
    const controller = new AbortController();
    mockRequest.mockResolvedValue({
      items: [],
      total: 0,
      next_cursor: null,
      has_more: false,
    });

    await listUserAssets('user-1', {
      source_type: 'generated',
      media_type: 'image',
      limit: 24,
      cursor: 'opaque-cursor',
    }, controller.signal);

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/admin/users/user-1/assets',
      params: {
        source_type: 'generated',
        media_type: 'image',
        limit: 24,
        cursor: 'opaque-cursor',
      },
      signal: controller.signal,
    });
  });

  it('posts asset IDs instead of client supplied URLs when downloading ZIP', async () => {
    localStorage.setItem('access_token', 'token');
    localStorage.setItem('current_org_id', 'org-1');
    const blob = new Blob(['zip']);
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(blob, {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="assets.zip"' },
    }));
    const createObjectURL = vi.fn(() => 'blob:assets');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    await downloadUserAssetsZip('user-1', ['asset-1', 'asset-2']);

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/users/user-1/assets/download-zip', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer token',
        'X-Org-Id': 'org-1',
      },
      body: JSON.stringify({ asset_ids: ['asset-1', 'asset-2'] }),
    });
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(createObjectURL.mock.calls[0][0]).toEqual(expect.objectContaining({ size: 13 }));
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:assets');

    fetchMock.mockRestore();
    click.mockRestore();
  });

  it('surfaces the server error detail for a rejected ZIP request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ detail: '资产不属于该用户' }),
      { status: 403, headers: { 'Content-Type': 'application/json' } },
    ));

    await expect(downloadUserAssetsZip('user-1', ['foreign-asset']))
      .rejects.toThrow('资产不属于该用户');

    fetchMock.mockRestore();
  });
});
