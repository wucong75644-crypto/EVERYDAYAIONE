/**
 * 图片空间 Tab — [上传 | 生成] 切换 + 网格 + 多选 + 批量 ZIP 下载
 *
 * 复用 useFileSelection（资产 ID 作为 key）+ adminUser.downloadUserAssetsZip
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { Download } from 'lucide-react';
import { Button } from '../../ui/Button';
import { useFileSelection } from '../../../hooks/useFileSelection';
import {
  listUserAssets,
  downloadUserAssetsZip,
  type UserAsset,
} from '../../../services/adminUser';
import { usePreview } from '../../../preview/usePreview';
import PreviewHost from '../../../preview/PreviewHost';
import type { PreviewItem } from '../../../preview/types';
import { UploadCard, GenerationCard } from './AssetCards';
import { pickOriginalImageUrl } from '../../../utils/imageUrlRules';

type Mode = 'upload' | 'generated';

interface Props {
  userId: string;
}

const PAGE_SIZE = 24;

export default function AssetSpaceTab({ userId }: Props) {
  const [mode, setMode] = useState<Mode>('upload');
  const [page, setPage] = useState(1);
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<(string | undefined)[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [assets, setAssets] = useState<UserAsset[]>([]);
  const [totals, setTotals] = useState({ uploads: 0, generations: 0 });
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  const sel = useFileSelection();
  const preview = usePreview();

  // 当前页所有可预览图片（按显示顺序，作为 lightbox 上下张轮播池）
  const previewItems = useMemo<PreviewItem[]>(() => {
    if (mode === 'upload') {
      return assets
        .filter((asset) => asset.media_type === 'image')
        .map((u) => ({
          url: pickOriginalImageUrl(u.original_url, u.download_url),
          thumbnailUrl: u.thumbnail_url || undefined,
          filename: u.name,
        }));
    }
    return assets
      .filter((asset) => asset.media_type === 'image')
      .map((g) => ({
        url: pickOriginalImageUrl(g.original_url, g.download_url),
        thumbnailUrl: g.thumbnail_url || undefined,
        filename: g.name,
      }));
  }, [mode, assets]);

  const openLightbox = useCallback((url: string) => {
    const idx = previewItems.findIndex((i) => i.url === url);
    if (idx < 0) return;
    preview.open(previewItems, idx);
  }, [previewItems, preview]);

  // 加载数据（cursor / mode 变化），取消旧请求避免竞态覆盖。
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      setLoading(true);
      try {
        const data = await listUserAssets(userId, {
          source_type: mode,
          limit: PAGE_SIZE,
          ...(cursor ? { cursor } : {}),
        }, controller.signal);
        setAssets(data.items);
        setNextCursor(data.next_cursor);
        setTotals((current) => ({
          ...current,
          [mode === 'upload' ? 'uploads' : 'generations']: data.total,
        }));
      } catch (error: unknown) {
        if (!controller.signal.aborted) toast.error(assetErrorMessage(error));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [userId, mode, cursor]);

  // 独立加载两个 Tab 的总数。
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const [uploads, generations] = await Promise.all([
          listUserAssets(userId, {
            source_type: 'upload', limit: 1,
          }, controller.signal),
          listUserAssets(userId, {
            source_type: 'generated', limit: 1,
          }, controller.signal),
        ]);
        setTotals({
          uploads: uploads.total,
          generations: generations.total,
        });
      } catch { /* silent */ }
    })();
    return () => controller.abort();
  }, [userId]);

  // 切换 mode 时清空选中 + 回到第一页
  const handleSwitchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    setPage(1);
    setCursor(undefined);
    setCursorHistory([]);
    setNextCursor(null);
    sel.clear();
  };

  const currentIds = useMemo(() => assets.map((asset) => asset.id), [assets]);
  const allSelected = currentIds.length > 0 && currentIds.every((id) => sel.isSelected(id));

  const handleToggleAll = () => {
    if (allSelected) sel.clear();
    else sel.selectAll(currentIds);
  };

  const handleDownloadSelected = useCallback(async () => {
    if (sel.selectedCount === 0) {
      toast.error('请先选中要下载的资产');
      return;
    }
    setDownloading(true);
    try {
      await downloadUserAssetsZip(
        userId, Array.from(sel.selectedPaths),
      );
      toast.success('下载已开始');
    } catch (error: unknown) {
      toast.error(assetErrorMessage(error));
    } finally {
      setDownloading(false);
    }
  }, [sel, userId]);

  const total = mode === 'upload' ? totals.uploads : totals.generations;

  const handleNextPage = () => {
    if (!nextCursor) return;
    setCursorHistory((history) => [...history, cursor]);
    setCursor(nextCursor);
    setPage((current) => current + 1);
    sel.clear();
  };

  const handlePreviousPage = () => {
    const previous = cursorHistory.at(-1);
    setCursorHistory((history) => history.slice(0, -1));
    setCursor(previous);
    setPage((current) => Math.max(1, current - 1));
    sel.clear();
  };

  return (
    <div className="flex flex-col gap-3">
      <PreviewHost
        state={preview.state}
        onClose={preview.close}
        onIndexChange={preview.setIndex}
      />
      {/* 顶部切换 */}
      <div className="flex gap-1 border-b border-[var(--s-border-default)]">
        <ModeTab
          active={mode === 'upload'}
          label="📤 上传"
          count={totals.uploads}
          onClick={() => handleSwitchMode('upload')}
        />
        <ModeTab
          active={mode === 'generated'}
          label="✨ 生成"
          count={totals.generations}
          onClick={() => handleSwitchMode('generated')}
        />
      </div>

      {/* 工具栏 */}
      <div className="flex items-center gap-2 text-sm">
        <label className="flex items-center gap-1.5 cursor-pointer text-[var(--s-text-secondary)]">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={handleToggleAll}
            disabled={currentIds.length === 0}
          />
          <span>全选本页</span>
        </label>
        <div className="text-[var(--s-text-tertiary)]">
          已选 {sel.selectedCount} / 共 {total} 个
        </div>
        <Button
          size="sm"
          variant="accent"
          className="ml-auto"
          icon={<Download className="w-3.5 h-3.5" />}
          disabled={sel.selectedCount === 0}
          loading={downloading}
          onClick={handleDownloadSelected}
        >
          下载选中 ZIP ({sel.selectedCount})
        </Button>
      </div>

      {/* 网格 */}
      <div>
        {loading ? (
          <div className="text-center py-12 text-[var(--s-text-tertiary)] text-sm">加载中...</div>
        ) : total === 0 ? (
          <div className="text-center py-12 text-[var(--s-text-tertiary)] text-sm">
            {mode === 'upload' ? '该用户无上传内容' : '该用户无生成内容'}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {mode === 'upload'
              ? assets.map((asset) => (
                  <UploadCard
                    key={asset.id}
                    asset={asset}
                    selected={sel.isSelected(asset.id)}
                    onToggle={() => sel.toggle(asset.id)}
                    onPreview={openLightbox}
                  />
                ))
              : assets.map((asset) => (
                  <GenerationCard
                    key={asset.id}
                    asset={asset}
                    selected={sel.isSelected(asset.id)}
                    onToggle={() => sel.toggle(asset.id)}
                    onPreview={openLightbox}
                  />
                ))}
          </div>
        )}
      </div>

      {/* 分页 */}
      {(page > 1 || nextCursor) && (
        <div className="flex items-center justify-between text-sm text-[var(--s-text-secondary)]">
          <span>第 {page} 页</span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={handlePreviousPage}>
              上一页
            </Button>
            <Button size="sm" variant="ghost" disabled={!nextCursor} onClick={handleNextPage}>
              下一页
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function assetErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败';
}

function ModeTab({ active, label, count, onClick }: {
  active: boolean; label: string; count: number; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
          : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]'
      }`}
    >
      {label} {count}
    </button>
  );
}
