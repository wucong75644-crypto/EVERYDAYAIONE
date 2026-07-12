import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request } from '../api';
import { attachDetailImage, getCurrentDetailProject, removeDetailImage, saveDetailSettings } from '../detailProject';

vi.mock('../api', () => ({ request: vi.fn() }));
const mockedRequest = vi.mocked(request);

beforeEach(() => mockedRequest.mockReset());

describe('detailProject service', () => {
  it('读取当前草稿', async () => {
    mockedRequest.mockResolvedValue({ success: true, data: { project: null } });
    await expect(getCurrentDetailProject()).resolves.toBeNull();
    expect(mockedRequest).toHaveBeenCalledWith({ url: '/detail-projects/current' });
  });

  it('关联工作区图片', async () => {
    mockedRequest.mockResolvedValue({ success: true, data: { project: { id: 'p1' } } });
    await attachDetailImage('上传/a.png', 'product');
    expect(mockedRequest).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST', data: { workspace_path: '上传/a.png', category: 'product' },
    }));
  });

  it('保存设置时转换字段名', async () => {
    mockedRequest.mockResolvedValue({ success: true, data: { project: { id: 'p1' } } });
    await saveDetailSettings('p1', 2, { contentType: 'main_image', platform: 'auto', requirement: '', language: 'zh-CN', aspectRatio: '1:1', quality: '1k', count: 3 });
    expect(mockedRequest).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({ version: 2, image_count: 3, content_type: 'main_image' }),
    }));
  });

  it('删除只发送项目引用和版本', async () => {
    mockedRequest.mockResolvedValue({ success: true, data: { project: { id: 'p1' } } });
    await removeDetailImage('p1', 'i1', 3);
    expect(mockedRequest).toHaveBeenCalledWith(expect.objectContaining({ method: 'DELETE', data: { version: 3 } }));
  });
});
