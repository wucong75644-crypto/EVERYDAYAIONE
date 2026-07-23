import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useWorkspace } from '../useWorkspace';
import {
  deleteWorkspaceItem,
  listWorkspace,
  mkdirWorkspace,
  moveWorkspaceItem,
  renameWorkspaceItem,
  uploadToWorkspace,
} from '../../services/workspace';

vi.mock('../../services/workspace', () => ({
  listWorkspace: vi.fn(),
  uploadToWorkspace: vi.fn(),
  deleteWorkspaceItem: vi.fn(),
  mkdirWorkspace: vi.fn(),
  renameWorkspaceItem: vi.fn(),
  moveWorkspaceItem: vi.fn(),
}));

const mockListWorkspace = vi.mocked(listWorkspace);
const mockDeleteWorkspaceItem = vi.mocked(deleteWorkspaceItem);
const mockMkdirWorkspace = vi.mocked(mkdirWorkspace);
const mockMoveWorkspaceItem = vi.mocked(moveWorkspaceItem);
const mockRenameWorkspaceItem = vi.mocked(renameWorkspaceItem);
const mockUploadToWorkspace = vi.mocked(uploadToWorkspace);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('useWorkspace directory navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('hides parent items and aborts the parent request when navigating', async () => {
    const parent = deferred<Awaited<ReturnType<typeof listWorkspace>>>();
    const child = deferred<Awaited<ReturnType<typeof listWorkspace>>>();
    mockListWorkspace.mockImplementation((path) => (
      path === '.' ? parent.promise : child.promise
    ));

    const { result } = renderHook(() => useWorkspace());
    expect(mockListWorkspace).toHaveBeenCalledTimes(1);
    const parentSignal = mockListWorkspace.mock.calls[0][1];

    act(() => result.current.navigateTo('上传'));

    expect(parentSignal?.aborted).toBe(true);
    expect(result.current.currentPath).toBe('上传');
    expect(result.current.items).toEqual([]);
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(mockListWorkspace).toHaveBeenCalledTimes(2));

    child.resolve({
      path: '上传',
      items: [{
        name: 'report.xlsx',
        is_dir: false,
        size: 10,
        modified: '1',
        cdn_url: null,
        mime_type: null,
      }],
      total: 1,
    });

    await waitFor(() => expect(result.current.items[0]?.name).toBe('report.xlsx'));
    expect(result.current.loading).toBe(false);
  });

  it('ignores an older response after a rapid directory switch', async () => {
    const requests = new Map<string, ReturnType<typeof deferred<Awaited<ReturnType<typeof listWorkspace>>>>>();
    mockListWorkspace.mockImplementation((path) => {
      const request = deferred<Awaited<ReturnType<typeof listWorkspace>>>();
      requests.set(path, request);
      return request.promise;
    });

    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.navigateTo('上传'));
    await waitFor(() => expect(requests.has('上传')).toBe(true));
    act(() => result.current.navigateTo('下载'));
    await waitFor(() => expect(requests.has('下载')).toBe(true));

    requests.get('上传')?.resolve({
      path: '上传',
      items: [],
      total: 0,
    });
    requests.get('下载')?.resolve({
      path: '下载',
      items: [],
      total: 0,
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.currentPath).toBe('下载');
    expect(result.current.error).toBeNull();
  });

  it('rejects a response whose path does not match the requested directory', async () => {
    mockListWorkspace.mockResolvedValue({
      path: '错误目录',
      items: [],
      total: 0,
    });

    const { result } = renderHook(() => useWorkspace());

    await waitFor(() => expect(result.current.error).toBe('工作区返回了不匹配的目录'));
    expect(result.current.items).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it('resets category and multi-select state when navigating', async () => {
    mockListWorkspace.mockImplementation(async (path) => ({
      path,
      items: [],
      total: 0,
    }));
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.setCategoryFilter('images');
      result.current.setMultiSelectMode(true);
    });
    expect(result.current.viewMode).toBe('grid');

    await act(async () => {
      result.current.navigateTo('上传');
      await Promise.resolve();
    });

    expect(result.current.categoryFilter).toBe('all');
    expect(result.current.multiSelectMode).toBe(false);
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it('keeps invalid folder names on the client side', async () => {
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let succeeded = true;
    await act(async () => {
      succeeded = await result.current.mkdir('../invalid');
    });

    expect(succeeded).toBe(false);
    expect(mockMkdirWorkspace).not.toHaveBeenCalled();
    expect(result.current.error).toContain('文件夹名称无效');
  });

  it('preserves upload progress behavior after extraction', async () => {
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    mockUploadToWorkspace.mockImplementation(async (_file, _path, onProgress) => {
      onProgress?.(50);
      return { filename: 'report.txt', path: 'report.txt', size: 3, cdn_url: null };
    });
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let succeeded = false;
    await act(async () => {
      succeeded = await result.current.upload([
        new File(['abc'], 'report.txt', { type: 'text/plain' }),
      ]);
    });

    expect(succeeded).toBe(true);
    expect(mockUploadToWorkspace).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'report.txt' }),
      '.',
      expect.any(Function),
    );
  });

  it('preserves workspace mutation behavior after extraction', async () => {
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    mockDeleteWorkspaceItem.mockResolvedValue(undefined);
    mockMkdirWorkspace.mockResolvedValue(undefined);
    mockRenameWorkspaceItem.mockResolvedValue(undefined);
    mockMoveWorkspaceItem.mockResolvedValue(undefined);
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      expect(await result.current.mkdir('资料')).toBe(true);
      expect(await result.current.rename('old.txt', 'new.txt')).toBe(true);
      expect(await result.current.move('new.txt', '归档')).toBe(true);
      expect(await result.current.remove('归档/new.txt')).toBe(true);
    });

    expect(mockMkdirWorkspace).toHaveBeenCalledWith('资料');
    expect(mockRenameWorkspaceItem).toHaveBeenCalledWith('old.txt', 'new.txt');
    expect(mockMoveWorkspaceItem).toHaveBeenCalledWith('new.txt', '归档');
    expect(mockDeleteWorkspaceItem).toHaveBeenCalledWith('归档/new.txt');
  });

  it('preserves view and sort preferences after extraction', async () => {
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.setViewMode('grid');
      result.current.toggleSort('name');
    });

    expect(result.current.viewMode).toBe('grid');
    expect(result.current.sortField).toBe('name');
    expect(result.current.sortOrder).toBe('asc');
    expect(localStorage.getItem('workspace_view_mode')).toBe('grid');
    expect(localStorage.getItem('workspace_sort_field')).toBe('name');
  });

  it('loads saved preferences and reverses the active sort', async () => {
    localStorage.setItem('workspace_view_mode', 'grid');
    localStorage.setItem('workspace_sort_field', 'name');
    localStorage.setItem('workspace_sort_order', 'asc');
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.viewMode).toBe('grid');
    expect(result.current.sortField).toBe('name');
    expect(result.current.sortOrder).toBe('asc');

    act(() => result.current.toggleSort('name'));
    expect(result.current.sortOrder).toBe('desc');
  });

  it('reports a mutation failure without refreshing the directory', async () => {
    mockListWorkspace.mockResolvedValue({ path: '.', items: [], total: 0 });
    mockDeleteWorkspaceItem.mockRejectedValue(new Error('无权删除'));
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.loading).toBe(false));
    mockListWorkspace.mockClear();

    let succeeded = true;
    await act(async () => {
      succeeded = await result.current.remove('protected.txt');
    });

    expect(succeeded).toBe(false);
    expect(result.current.error).toBe('无权删除');
    expect(result.current.operatingPath).toBeNull();
    expect(mockListWorkspace).not.toHaveBeenCalled();
  });
});
