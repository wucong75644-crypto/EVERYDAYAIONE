/**
 * 图片空间 Tab — [上传 | 生成] 切换 + 网格 + 多选 + 批量 ZIP 下载
 *
 * 复用 useFileSelection（URL 作为 key）+ adminUser.downloadUserAssetsZip
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { Download } from 'lucide-react';
import { Button } from '../../ui/Button';
import { useFileSelection } from '../../../hooks/useFileSelection';
import {
  listUserUploads,
  listUserGenerations,
  downloadUserAssetsZip,
  type UploadAsset,
  type GenerationAsset,
} from '../../../services/adminUser';
import { usePreview } from '../../../preview/usePreview';
import PreviewHost from '../../../preview/PreviewHost';
import type { PreviewItem } from '../../../preview/types';
import { UploadCard, GenerationCard } from './AssetCards';
import { toOriginalImageUrl } from '../../../utils/imageUrlRules';

type Mode = 'uploads' | 'generations';

interface Props {
  userId: string;
}

const PAGE_SIZE = 24;

export default function AssetSpaceTab({ userId }: Props) {
  const [mode, setMode] = useState<Mode>('uploads');
  const [page, setPage] = useState(1);
  const [uploads, setUploads] = useState<UploadAsset[]>([]);
  const [generations, setGenerations] = useState<GenerationAsset[]>([]);
  const [totals, setTotals] = useState({ uploads: 0, generations: 0 });
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);

  const sel = useFileSelection();
  const preview = usePreview();

  // 当前页所有可预览图片（按显示顺序，作为 lightbox 上下张轮播池）
  const previewItems = useMemo<PreviewItem[]>(() => {
    if (mode === 'uploads') {
      return uploads
        .filter((u) => u.type === 'image')
        .map((u) => ({
          url: toOriginalImageUrl(u.original_url || u.download_url || u.url),
          thumbnailUrl: u.thumbnail_url || undefined,
          filename: u.name,
        }));
    }
    return generations
      .filter((g) => g.kind === 'image')
      .map((g) => ({
        url: toOriginalImageUrl(g.original_url || g.download_url || g.url),
        thumbnailUrl: g.thumbnail_url || undefined,
        filename: `${g.id}.jpg`,
      }));
  }, [mode, uploads, generations]);

  const openLightbox = useCallback((url: string) => {
    const idx = previewItems.findIndex((i) => i.url === url);
    if (idx < 0) return;
    preview.open(previewItems, idx);
  }, [previewItems, preview]);

  // 加载数据（page / mode 变化）
  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        if (mode === 'uploads') {
          const data = await listUserUploads(userId, { page, page_size: PAGE_SIZE });
          setUploads(data.items);
          setTotals((t) => ({ ...t, uploads: data.total }));
        } else {
          const data = await listUserGenerations(userId, { page, page_size: PAGE_SIZE });
          setGenerations(data.items);
          setTotals((t) => ({ ...t, generations: data.total }));
        }
      } catch (err: any) {
        toast.error(err?.response?.data?.detail || '加载资产失败');
      } finally {
        setLoading(false);
      }
    })();
  }, [userId, mode, page]);

  // 同时加载另一个 tab 的 total（首次）
  useEffect(() => {
    (async () => {
      try {
        if (mode === 'uploads' && totals.generations === 0) {
          const data = await listUserGenerations(userId, { page: 1, page_size: 1 });
          setTotals((t) => ({ ...t, generations: data.total }));
        }
        if (mode === 'generations' && totals.uploads === 0) {
          const data = await listUserUploads(userId, { page: 1, page_size: 1 });
          setTotals((t) => ({ ...t, uploads: data.total }));
        }
      } catch { /* silent */ }
    })();
  }, [userId, mode, totals.uploads, totals.generations]);

  // 切换 mode 时清空选中 + 回到第一页
  const handleSwitchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    setPage(1);
    sel.clear();
  };

  const currentItems = useMemo(() => {
    return mode === 'uploads'
      ? uploads.map((u) => ({ url: toOriginalImageUrl(u.download_url || u.original_url || u.url), name: u.name }))
      : generations.map((g) => ({ url: toOriginalImageUrl(g.download_url || g.original_url || g.url), name: g.id }));
  }, [mode, uploads, generations]);

  const currentUrls = useMemo(() => currentItems.map((i) => i.url), [currentItems]);
  const allSelected = currentUrls.length > 0 && currentUrls.every((u) => sel.isSelected(u));

  const handleToggleAll = () => {
    if (allSelected) sel.clear();
    else sel.selectAll(currentUrls);
  };

  const handleDownloadSelected = useCallback(async () => {
    if (sel.selectedCount === 0) {
      toast.error('请先选中要下载的资产');
      return;
    }
    setDownloading(true);
    try {
      const urls = Array.from(sel.selectedPaths);
      await downloadUserAssetsZip(userId, {
        urls,
        zip_name: `${mode}-${urls.length}.zip`,
      });
      toast.success('下载已开始');
    } catch (err: any) {
      toast.error(err?.message || '下载失败');
    } finally {
      setDownloading(false);
    }
  }, [sel, userId, mode]);

  const total = mode === 'uploads' ? totals.uploads : totals.generations;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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
          active={mode === 'uploads'}
          label="📤 上传"
          count={totals.uploads}
          onClick={() => handleSwitchMode('uploads')}
        />
        <ModeTab
          active={mode === 'generations'}
          label="✨ 生成"
          count={totals.generations}
          onClick={() => handleSwitchMode('generations')}
        />
      </div>

      {/* 工具栏 */}
      <div className="flex items-center gap-2 text-sm">
        <label className="flex items-center gap-1.5 cursor-pointer text-[var(--s-text-secondary)]">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={handleToggleAll}
            disabled={currentUrls.length === 0}
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
            {mode === 'uploads' ? '该用户无上传内容' : '该用户无生成内容'}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {mode === 'uploads'
              ? uploads.map((a) => (
                  <UploadCard
                    key={a.message_id + a.url}
                    asset={a}
                    selected={sel.isSelected(a.download_url || a.original_url || a.url)}
                    onToggle={() => sel.toggle(a.download_url || a.original_url || a.url)}
                    onPreview={openLightbox}
                  />
                ))
              : generations.map((g) => (
                  <GenerationCard
                    key={g.id + g.url}
                    asset={g}
                    selected={sel.isSelected(g.download_url || g.original_url || g.url)}
                    onToggle={() => sel.toggle(g.download_url || g.original_url || g.url)}
                    onPreview={openLightbox}
                  />
                ))}
          </div>
        )}
      </div>

      {/* 分页 */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-[var(--s-text-secondary)]">
          <span>第 {page} / {totalPages} 页</span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <Button size="sm" variant="ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
              下一页
            </Button>
          </div>
        </div>
      )}
    </div>
  );
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
