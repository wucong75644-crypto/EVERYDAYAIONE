import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AssetSpaceTab from '../AssetSpaceTab';
import type { UserAsset, UserAssetsResponse } from '../../../../services/adminUser';

vi.mock('../../../../services/adminUser', () => ({
  listUserAssets: vi.fn(),
  downloadUserAssetsZip: vi.fn(),
}));

vi.mock('../AssetCards', () => ({
  UploadCard: ({
    asset,
    selected,
    onToggle,
  }: {
    asset: UserAsset;
    selected: boolean;
    onToggle: () => void;
  }) => (
    <button type="button" aria-pressed={selected} onClick={onToggle}>
      {asset.name}
    </button>
  ),
  GenerationCard: ({
    asset,
    selected,
    onToggle,
  }: {
    asset: UserAsset;
    selected: boolean;
    onToggle: () => void;
  }) => (
    <button type="button" aria-pressed={selected} onClick={onToggle}>
      {asset.name}
    </button>
  ),
}));

vi.mock('../../../../preview/PreviewHost', () => ({ default: () => null }));
vi.mock('../../../../preview/usePreview', () => ({
  usePreview: () => ({
    state: { isOpen: false, items: [], index: 0 },
    open: vi.fn(),
    close: vi.fn(),
    setIndex: vi.fn(),
  }),
}));

import {
  downloadUserAssetsZip,
  listUserAssets,
} from '../../../../services/adminUser';

const mockListUserAssets = vi.mocked(listUserAssets);
const mockDownloadUserAssetsZip = vi.mocked(downloadUserAssetsZip);

function asset(id: string, sourceType: 'upload' | 'generated'): UserAsset {
  return {
    id,
    source_type: sourceType,
    source_kind: sourceType === 'upload' ? 'web_upload' : 'image_task',
    media_type: 'image',
    status: 'ready',
    original_url: `https://cdn.example.com/${id}.png`,
    thumbnail_url: null,
    download_url: `https://cdn.example.com/${id}.png`,
    workspace_path: null,
    name: `${id}.png`,
    size: 1024,
    mime_type: 'image/png',
    conversation_id: null,
    source_message_id: null,
    source_task_id: null,
    model_id: null,
    prompt: null,
    metadata: {},
    created_at: '2026-07-20T00:00:00Z',
  };
}

function response(
  items: UserAsset[],
  total: number,
  nextCursor: string | null = null,
): UserAssetsResponse {
  return {
    items,
    total,
    next_cursor: nextCursor,
    has_more: nextCursor !== null,
  };
}

describe('AssetSpaceTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListUserAssets.mockImplementation(async (_uid, params) => {
      if (params.source_type === 'generated') {
        return params.limit === 1
          ? response([], 2)
          : response([asset('generated-1', 'generated')], 2);
      }
      if (params.limit === 1) return response([], 3);
      if (params.cursor === 'next-upload') return response([asset('asset-2', 'upload')], 3);
      return response([asset('asset-1', 'upload')], 3, 'next-upload');
    });
  });

  it('loads the unified upload asset list and downloads selected asset IDs', async () => {
    const user = userEvent.setup();
    render(<AssetSpaceTab userId="user-1" />);

    const card = await screen.findByRole('button', { name: 'asset-1.png' });
    await user.click(card);
    await user.click(screen.getByRole('button', { name: /下载选中 ZIP/ }));

    expect(mockDownloadUserAssetsZip).toHaveBeenCalledWith('user-1', ['asset-1']);
    expect(screen.queryByText('下载本对话全部素材')).not.toBeInTheDocument();
  });

  it('uses the opaque next cursor and restores the previous cursor', async () => {
    const user = userEvent.setup();
    render(<AssetSpaceTab userId="user-1" />);

    await screen.findByRole('button', { name: 'asset-1.png' });
    await user.click(screen.getByRole('button', { name: '下一页' }));

    await screen.findByRole('button', { name: 'asset-2.png' });
    expect(mockListUserAssets).toHaveBeenCalledWith(
      'user-1',
      expect.objectContaining({ source_type: 'upload', cursor: 'next-upload' }),
      expect.any(AbortSignal),
    );

    await user.click(screen.getByRole('button', { name: '上一页' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'asset-1.png' })).toBeInTheDocument();
    });
    expect(screen.getByText('第 1 页')).toBeInTheDocument();
  });

  it('switches to generated assets and clears the upload cursor', async () => {
    const user = userEvent.setup();
    render(<AssetSpaceTab userId="user-1" />);

    await screen.findByRole('button', { name: 'asset-1.png' });
    await user.click(screen.getByRole('button', { name: /生成/ }));

    await waitFor(() => {
      expect(mockListUserAssets).toHaveBeenCalledWith(
        'user-1',
        { source_type: 'generated', limit: 24 },
        expect.any(AbortSignal),
      );
    });
    expect(screen.getByRole('button', { name: 'generated-1.png' })).toBeInTheDocument();
  });
});
