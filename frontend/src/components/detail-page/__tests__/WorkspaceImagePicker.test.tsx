import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceImagePicker } from '../WorkspaceImagePicker';
import { listWorkspace } from '../../../services/workspace';

vi.mock('../../../services/workspace', () => ({
  listWorkspace: vi.fn(), searchWorkspace: vi.fn(),
  getWorkspacePreviewUrl: (path: string) => `/preview/${path}`,
}));

describe('WorkspaceImagePicker', () => {
  it('只展示支持的图片并返回选择路径', async () => {
    vi.mocked(listWorkspace).mockResolvedValue({ path: '上传', total: 2, items: [
      { name: 'product.png', is_dir: false, size: 1, modified: '', cdn_url: null, mime_type: 'image/png' },
      { name: 'notes.txt', is_dir: false, size: 1, modified: '', cdn_url: null, mime_type: 'text/plain' },
    ] });
    const onSelect = vi.fn();
    render(<WorkspaceImagePicker open remaining={2} onClose={vi.fn()} onSelect={onSelect} />);
    const image = await screen.findByRole('img', { name: 'product.png' });
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument();
    fireEvent.click(image);
    fireEvent.click(screen.getByRole('button', { name: /添加 1/ }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(['上传/product.png']));
  });
});
