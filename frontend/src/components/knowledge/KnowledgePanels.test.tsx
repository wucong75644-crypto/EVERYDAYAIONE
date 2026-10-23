import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeHistory } from './KnowledgeHistory';
import { KnowledgeSearch } from './KnowledgeSearch';
const api = vi.hoisted(() => ({ versions: vi.fn(), version: vi.fn(), search: vi.fn() }));
vi.mock('../../services/knowledge', () => ({ knowledgeApi: api }));
beforeEach(() => {
  vi.resetAllMocks();
  api.versions.mockResolvedValue({ items: [{ revision: 2, title: '旧标题', created_at: '2026-10-22T00:00:00Z' }], next_cursor: null });
  api.version.mockResolvedValue({ revision: 2, title: '旧标题', body: '不可变的旧正文' });
  api.search.mockResolvedValue({ items: [{ id: 'n', title: '项目说明', excerpt: '正文包含中文关键词' }], next_cursor: null });
});
describe('V2 knowledge panels', () => {
  it('loads a historical body as read-only without a mutation or restore action', async () => {
    render(<KnowledgeHistory objectId="object" />);
    fireEvent.click(screen.getByText('查看历史版本'));
    fireEvent.click(await screen.findByRole('button', { name: /版本 2/ }));
    expect(await screen.findByLabelText('历史版本正文')).toHaveValue('不可变的旧正文');
    expect(screen.getByLabelText('历史版本正文')).toHaveAttribute('readonly');
    expect(screen.queryByText('恢复版本')).not.toBeInTheDocument();
  });
  it('clears inaccessible historical detail after a failed list request', async () => {
    api.versions.mockRejectedValue(new Error('permission revoked'));
    render(<KnowledgeHistory objectId="object" />);
    fireEvent.click(screen.getByText('查看历史版本'));
    await screen.findByRole('alert');
    expect(screen.queryByLabelText('历史版本正文')).not.toBeInTheDocument();
  });
  it('finds a body match and locates it on the canvas', async () => {
    const locate = vi.fn();
    render(<KnowledgeSearch containerId="project" query="中文关键词" onLocate={locate} />);
    fireEvent.click(await screen.findByRole('button', { name: /项目说明/ }));
    expect(locate).toHaveBeenCalledWith('n');
    expect(api.search).toHaveBeenCalledWith('project', '中文关键词', undefined, expect.any(AbortSignal));
  });
  it('aborts on scope removal and ignores a late response', async () => {
    let resolve!: (v: unknown) => void;
    api.search.mockReturnValue(new Promise(r => { resolve = r; }));
    const view = render(<KnowledgeSearch containerId="old" query="词" onLocate={vi.fn()} />);
    await waitFor(() => expect(api.search).toHaveBeenCalled());
    const signal = api.search.mock.calls[0][3];
    view.unmount(); expect(signal.aborted).toBe(true);
    resolve({ items: [{ id: 'secret', title: '旧企业私密内容', excerpt: '' }], next_cursor: null });
    expect(screen.queryByText('旧企业私密内容')).not.toBeInTheDocument();
  });
});
