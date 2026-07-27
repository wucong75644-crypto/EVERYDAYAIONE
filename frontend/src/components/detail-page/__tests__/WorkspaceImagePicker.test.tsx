import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceImagePicker } from '../WorkspaceImagePicker';
import { listWorkspace, searchWorkspace, type WorkspaceFileItem } from '../../../services/workspace';

vi.mock('../../../services/workspace', () => ({
  listWorkspace: vi.fn(), searchWorkspace: vi.fn(),
  getWorkspacePreviewUrl: (path: string) => `/preview/${path}`,
}));

const thumbnailUrl = 'https://oss.example.com/workspace-thumbnails/product.w360.webp';
const cdnUrl = 'https://oss.example.com/workspace/product.png';
const workspacePath = '上传/product.png';
const proxyUrl = `/preview/${workspacePath}`;

function imageItem(overrides: Partial<WorkspaceFileItem> = {}): WorkspaceFileItem {
  return {
    name: 'product.png',
    is_dir: false,
    size: 1,
    modified: '1',
    cdn_url: cdnUrl,
    thumbnail_url: thumbnailUrl,
    mime_type: 'image/png',
    ...overrides,
  };
}

function mockList(item: WorkspaceFileItem): void {
  vi.mocked(listWorkspace).mockResolvedValue({ path: '上传', total: 1, items: [item] });
}

describe('WorkspaceImagePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

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

  it('thumbnail_url 成功时只使用缩略图', async () => {
    mockList(imageItem());
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    expect(await screen.findByRole('img', { name: 'product.png' })).toHaveAttribute('src', thumbnailUrl);
  });

  it('按 thumbnail_url、cdn_url、工作区代理、失败占位顺序降级', async () => {
    mockList(imageItem());
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    let image = await screen.findByRole('img', { name: 'product.png' });
    fireEvent.error(image);
    image = screen.getByRole('img', { name: 'product.png' });
    expect(image).toHaveAttribute('src', cdnUrl);

    fireEvent.error(image);
    image = screen.getByRole('img', { name: 'product.png' });
    expect(image).toHaveAttribute('src', proxyUrl);

    fireEvent.error(image);
    expect(screen.queryByRole('img', { name: 'product.png' })).not.toBeInTheDocument();
    expect(screen.getByText('图片加载失败')).toBeInTheDocument();
  });

  it('thumbnail_url 缺失时从 cdn_url 开始', async () => {
    mockList(imageItem({ thumbnail_url: null }));
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    expect(await screen.findByRole('img', { name: 'product.png' })).toHaveAttribute('src', cdnUrl);
  });

  it('cdn_url 缺失时从工作区代理开始', async () => {
    mockList(imageItem({ thumbnail_url: null, cdn_url: null }));
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    expect(await screen.findByRole('img', { name: 'product.png' })).toHaveAttribute('src', proxyUrl);
  });

  it('拒绝把 workspace-thumbnails cdn_url 当作原图候选', async () => {
    mockList(imageItem({ thumbnail_url: null, cdn_url: thumbnailUrl }));
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    expect(await screen.findByRole('img', { name: 'product.png' })).toHaveAttribute('src', proxyUrl);
  });

  it('item URL 或 workspacePath 变化后从新候选链首项开始', async () => {
    mockList(imageItem());
    vi.mocked(searchWorkspace).mockResolvedValue({
      total: 1,
      items: [{
        ...imageItem({
          modified: '2',
          cdn_url: 'https://oss.example.com/workspace/updated.png',
          thumbnail_url: 'https://oss.example.com/workspace-thumbnails/updated.w360.webp',
        }),
        workspace_path: '更新/product.png',
      }],
    });
    render(<WorkspaceImagePicker open remaining={1} onClose={vi.fn()} onSelect={vi.fn()} />);

    const image = await screen.findByRole('img', { name: 'product.png' });
    fireEvent.error(image);
    fireEvent.error(screen.getByRole('img', { name: 'product.png' }));
    expect(screen.getByRole('img', { name: 'product.png' })).toHaveAttribute('src', proxyUrl);

    fireEvent.change(screen.getByRole('textbox', { name: '搜索工作区图片' }), {
      target: { value: 'product' },
    });
    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'product.png' })).toHaveAttribute(
        'src',
        'https://oss.example.com/workspace-thumbnails/updated.w360.webp',
      );
    });
  });
});
