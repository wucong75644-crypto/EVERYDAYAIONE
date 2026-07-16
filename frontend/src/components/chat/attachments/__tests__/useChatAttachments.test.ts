import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useChatAttachments } from '../useChatAttachments';

vi.mock('../../../../services/upload', () => ({
  uploadImageFile: vi.fn(async (file: File) => ({
    url: `https://cdn.example.com/${file.name}`,
    original_url: `https://cdn.example.com/${file.name}`,
    name: file.name,
    mime_type: file.type,
    size: file.size,
  })),
}));

vi.mock('../../../../services/fileUpload', () => ({
  uploadFile: vi.fn(async (file: File) => ({
    url: `https://cdn.example.com/${file.name}`,
    name: file.name,
    workspace_path: `上传/${file.name}`,
  })),
}));

const workspaceImage = {
  name: 'workspace.png',
  workspace_path: '上传/workspace.png',
  cdn_url: 'https://cdn.example.com/workspace.png',
  mime_type: 'image/png',
  size: 10,
};

describe('useChatAttachments', () => {
  it('通过统一本地入口分流并上传图片与文件', async () => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
    const { result } = renderHook(() => useChatAttachments());

    await act(async () => result.current.addLocalFiles([
      new File(['image'], 'product.png', { type: 'image/png' }),
      new File(['pdf'], 'report.pdf', { type: 'application/pdf' }),
    ], { maxImages: 2, maxImageSizeMB: 10, maxFileSizeMB: 20 }));

    expect(result.current.attachments).toHaveLength(2);
    expect(result.current.attachments.map((item) => item.kind)).toEqual(['image', 'file']);
    expect(result.current.attachments.every((item) => item.status === 'ready')).toBe(true);
  });

  it('通过统一入口添加引用图片和工作区图片', () => {
    const { result } = renderHook(() => useChatAttachments());

    act(() => result.current.addQuotedImage({ url: 'https://cdn.example.com/quote.png' }));
    act(() => result.current.addWorkspaceFile(workspaceImage));

    expect(result.current.attachments).toHaveLength(2);
    expect(result.current.attachments.map((item) => item.source)).toEqual(['quote', 'workspace']);
    expect(result.current.hasImages).toBe(true);
    expect(result.current.hasQuotedImage).toBe(true);
    expect(result.current.readyImageCount).toBe(2);
  });

  it('按工作区路径去重', () => {
    const { result } = renderHook(() => useChatAttachments());

    act(() => {
      result.current.addWorkspaceFile(workspaceImage);
      result.current.addWorkspaceFile({ ...workspaceImage });
    });

    expect(result.current.attachments.filter((item) => item.source === 'workspace')).toHaveLength(1);
  });

  it('使用统一附件 ID 删除对应来源', () => {
    const { result } = renderHook(() => useChatAttachments());
    act(() => result.current.addQuotedImage({ url: 'https://cdn.example.com/quote.png' }));
    act(() => result.current.addWorkspaceFile(workspaceImage));

    const quote = result.current.attachments.find((item) => item.source === 'quote');
    const workspace = result.current.attachments.find((item) => item.source === 'workspace');
    act(() => result.current.removeAttachment(quote!.id));
    act(() => result.current.removeAttachment(workspace!.id));

    expect(result.current.attachments).toEqual([]);
  });

  it('统一移出并在拒绝后合并恢复等待期间的新附件', () => {
    const { result } = renderHook(() => useChatAttachments());
    act(() => result.current.addQuotedImage({ url: 'https://cdn.example.com/submitted.png' }));
    act(() => result.current.addWorkspaceFile(workspaceImage));

    let restore = () => undefined;
    act(() => { restore = result.current.detachForSubmission().restore; });
    expect(result.current.attachments).toEqual([]);

    act(() => result.current.addQuotedImage({ url: 'https://cdn.example.com/new.png' }));
    act(() => restore());

    expect(result.current.attachments).toHaveLength(3);
    expect(result.current.attachments.filter((item) => item.source === 'quote')).toHaveLength(2);
    expect(result.current.attachments.some((item) => item.source === 'workspace')).toBe(true);
  });

  it('清除图片时保留普通工作区文件', () => {
    const { result } = renderHook(() => useChatAttachments());
    act(() => result.current.addWorkspaceFile(workspaceImage));
    act(() => result.current.addWorkspaceFile({
      name: 'report.pdf', workspace_path: '上传/report.pdf',
      cdn_url: 'https://cdn.example.com/report.pdf', mime_type: 'application/pdf', size: 20,
    }));

    act(() => result.current.clearImages());

    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0]).toMatchObject({ kind: 'file', name: 'report.pdf' });
  });

  it('空数组不触发上传，并可按统一 ID 删除本地图片和文件', async () => {
    const { result } = renderHook(() => useChatAttachments());
    await act(async () => result.current.addLocalFiles([]));
    expect(result.current.attachments).toEqual([]);

    await act(async () => result.current.addLocalFiles([
      new File(['image'], 'product.png', { type: 'image/png' }),
      new File(['pdf'], 'report.pdf', { type: 'application/pdf' }),
    ]));
    const ids = result.current.attachments.map((item) => item.id);
    act(() => ids.forEach(result.current.removeAttachment));

    expect(result.current.attachments).toEqual([]);
  });

  it('统一清除上传错误', async () => {
    const { result } = renderHook(() => useChatAttachments());
    await act(async () => result.current.addLocalFiles([
      new File(['bad'], 'malware.exe', { type: 'application/octet-stream' }),
    ]));
    expect(result.current.uploadError).toMatch(/不支持/);

    act(() => result.current.clearUploadErrors());
    expect(result.current.uploadError).toBeNull();
  });
});
