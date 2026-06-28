/**
 * 图片空间 Tab — [上传 | 生成] 切换 + 网格 + 多选 + 批量 ZIP 下载
 *
 * 复用 useFileSelection（URL 作为 key）+ adminUser.downloadUserAssetsZip
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download } from 'lucide-react';
import { Button } from '../../ui/Button';
import { useFileSelection } from '../../../hooks/useFileSelection';
import {
  listUserUploads,
  listUserGenerations,
  downloadUserAssetsZip,
  type UploadAsset,
  type GenerationAsset,
} from '../../../services/adminUser';
import { formatRelativeCN } from '../../../utils/formatRelativeCN';
import { downloadFile } from '../../../utils/downloadFile';

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
      ? uploads.map((u) => ({ url: u.url, name: u.name }))
      : generations.map((g) => ({ url: g.url, name: g.id }));
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
                    selected={sel.isSelected(a.url)}
                    onToggle={() => sel.toggle(a.url)}
                  />
                ))
              : generations.map((g) => (
                  <GenerationCard
                    key={g.id + g.url}
                    asset={g}
                    selected={sel.isSelected(g.url)}
                    onToggle={() => sel.toggle(g.url)}
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


// ── 上传卡片 ─────────────────────────────────────────────


function UploadCard({
  asset,
  selected,
  onToggle,
}: {
  asset: UploadAsset;
  selected: boolean;
  onToggle: () => void;
}) {
  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    downloadFile(asset.url, asset.name).catch((err) => toast.error(err?.message || '下载失败'));
  };

  return (
    <div
      className={`relative border rounded-lg overflow-hidden group cursor-pointer transition-colors ${
        selected ? 'border-[var(--s-accent)] ring-2 ring-[var(--s-accent)]/30' : 'border-[var(--s-border-default)]'
      }`}
      onClick={onToggle}
    >
      {/* 缩略图 / 文件图标 */}
      {asset.type === 'image' ? (
        <img src={asset.url} alt={asset.name} className="w-full aspect-square object-cover" />
      ) : (
        <div className="w-full aspect-square bg-[var(--s-bg-secondary)] flex items-center justify-center text-4xl">
          📄
        </div>
      )}

      {/* 多选框 */}
      <div className="absolute top-2 left-2">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          className="cursor-pointer"
          aria-label={`选中 ${asset.name}`}
        />
      </div>

      {/* 下载按钮 */}
      <button
        type="button"
        onClick={handleDownload}
        className="absolute top-2 right-2 p-1.5 bg-black/60 hover:bg-black/80 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"
        aria-label="下载"
      >
        <Download className="w-3.5 h-3.5" />
      </button>

      {/* 元数据 */}
      <div className="p-2 text-xs space-y-0.5">
        <div className="truncate font-medium" title={asset.name}>{asset.name}</div>
        <div className="flex justify-between text-[var(--s-text-tertiary)]">
          <span>{asset.size ? `${(asset.size / 1024).toFixed(0)} KB` : '—'}</span>
          <span>{formatRelativeCN(asset.created_at)}</span>
        </div>
      </div>
    </div>
  );
}


// ── 生成卡片 ─────────────────────────────────────────────


function GenerationCard({
  asset,
  selected,
  onToggle,
}: {
  asset: GenerationAsset;
  selected: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    const ext = asset.kind === 'video' ? 'mp4' : 'jpg';
    downloadFile(asset.url, `${asset.id}.${ext}`).catch((err) => toast.error(err?.message || '下载失败'));
  };

  const handleCopyPrompt = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!asset.prompt) return;
    navigator.clipboard.writeText(asset.prompt);
    toast.success('已复制提示词');
  };

  return (
    <div
      className={`relative border rounded-lg overflow-hidden group cursor-pointer transition-colors ${
        selected ? 'border-[var(--s-accent)] ring-2 ring-[var(--s-accent)]/30' : 'border-[var(--s-border-default)]'
      }`}
      onClick={onToggle}
    >
      {/* 缩略图 */}
      {asset.kind === 'image' ? (
        <img src={asset.url} alt={asset.prompt || ''} className="w-full aspect-square object-cover" />
      ) : (
        <div className="relative w-full aspect-square bg-black flex items-center justify-center">
          <video src={asset.url} className="w-full h-full object-cover" muted preload="metadata" />
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-10 h-10 rounded-full bg-white/30 backdrop-blur flex items-center justify-center">
              <span className="text-white text-xl">▶</span>
            </div>
          </div>
        </div>
      )}

      {/* 类型徽章 */}
      <div className="absolute top-2 left-2 flex items-center gap-2">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          className="cursor-pointer"
          aria-label="选中"
        />
        <span className="text-[10px] px-1.5 py-0.5 bg-black/60 text-white rounded">
          {asset.kind === 'image' ? '🖼' : '🎬'}
        </span>
      </div>

      {/* 下载按钮 */}
      <button
        type="button"
        onClick={handleDownload}
        className="absolute top-2 right-2 p-1.5 bg-black/60 hover:bg-black/80 text-white rounded opacity-0 group-hover:opacity-100 transition-opacity"
        aria-label="下载"
      >
        <Download className="w-3.5 h-3.5" />
      </button>

      {/* 元数据 + 提示词 */}
      <div className="p-2 text-xs space-y-1">
        {/* 提示词 */}
        {asset.prompt ? (
          <div className="bg-[var(--s-bg-secondary)] rounded p-1.5">
            <div
              className={`text-[var(--s-text-secondary)] break-words ${
                expanded ? '' : 'line-clamp-2'
              }`}
            >
              {asset.prompt}
            </div>
            <div className="flex items-center justify-between mt-1">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(!expanded);
                }}
                className="text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)] text-[10px]"
              >
                {expanded ? '收起' : '展开'}
              </button>
              <button
                type="button"
                onClick={handleCopyPrompt}
                className="text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)]"
                aria-label="复制提示词"
                title="复制提示词"
              >
                <Copy className="w-3 h-3" />
              </button>
            </div>
          </div>
        ) : (
          <div className="text-[var(--s-text-tertiary)] italic">无提示词</div>
        )}

        <div className="flex justify-between text-[var(--s-text-tertiary)]">
          <span className="truncate">{asset.model_id || '—'}</span>
          <span>💰 {asset.credits_cost}</span>
        </div>
        <div className="text-[var(--s-text-tertiary)]">{formatRelativeCN(asset.created_at)}</div>
      </div>
    </div>
  );
}
