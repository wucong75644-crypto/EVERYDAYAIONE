import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { downloadWorkspaceZip } from '../workspace';

const fetchMock = vi.fn();
const createObjectURL = vi.fn(() => 'blob:download');
const revokeObjectURL = vi.fn();
const OriginalURL = globalThis.URL;

describe('工作区分类 ZIP 下载', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', class extends OriginalURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('复用认证下载接口，发送分类路径并使用服务端中文 ZIP 文件名', async () => {
    localStorage.setItem('access_token', 'test-token');
    localStorage.setItem('current_org_id', 'org-test');
    const blob = new Blob(['zip']);
    fetchMock.mockResolvedValue({ok: true, blob: async () => blob,
      headers: new Headers({'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent('主图详情.zip')}`})});
    const anchor = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) {
      expect(this.download).toBe('主图详情.zip');
      expect(this.href).toBe('blob:download');
    });
    await downloadWorkspaceZip(['images/a.png'], {archivePaths: ['主图/01-首图.png'], archiveName: '主图详情.zip'});
    expect(fetchMock).toHaveBeenCalledWith('/api/files/workspace/download_zip', {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer test-token', 'X-Org-Id': 'org-test'},
      body: JSON.stringify({paths: ['images/a.png'], archive_paths: ['主图/01-首图.png'], archive_name: '主图详情.zip'}),
    });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(anchor).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:download');
  });

  it('原有调用保持请求格式，服务器失败时不创建空 ZIP 下载', async () => {
    fetchMock.mockResolvedValue({ok: false, json: async () => ({error: {message: '文件已被删除'}})});
    await expect(downloadWorkspaceZip(['images/a.png'])).rejects.toThrow('文件已被删除');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({paths: ['images/a.png']});
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
