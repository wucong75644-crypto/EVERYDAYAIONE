/**
 * 资产卡片组件（从 AssetSpaceTab 拆出，避免单文件超过 500 行硬约束）
 *
 * - UploadCard：用户上传的图片/文件
 * - GenerationCard：AI 生成的图片/视频（含提示词折叠 + 复制）
 *
 * 行为：
 * - 缩略图点击 → onPreview(url) 打开 lightbox（仅图片；视频走 toggle 选中）
 * - 右上 hover 操作按钮组：放大 / 下载
 * - 左上多选框：toggle 选中
 */

import { useState } from 'react';
import toast from 'react-hot-toast';
import { Copy, Download, ZoomIn } from 'lucide-react';
import type { UploadAsset, GenerationAsset } from '../../../services/adminUser';
import { formatRelativeCN } from '../../../utils/formatRelativeCN';
import { downloadFile } from '../../../utils/downloadFile';
import { ossThumbUrl } from '../../../utils/ossThumbUrl';


export function UploadCard({
  asset,
  selected,
  onToggle,
  onPreview,
}: {
  asset: UploadAsset;
  selected: boolean;
  onToggle: () => void;
  onPreview: (url: string) => void;
}) {
  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    downloadFile(asset.url, asset.name).catch((err) => toast.error(err?.message || '下载失败'));
  };

  const handlePreview = (e: React.MouseEvent) => {
    e.stopPropagation();
    onPreview(asset.url);
  };

  return (
    <div
      className={`relative border rounded-lg overflow-hidden group transition-colors ${
        selected ? 'border-[var(--s-accent)] ring-2 ring-[var(--s-accent)]/30' : 'border-[var(--s-border-default)]'
      }`}
    >
      {asset.type === 'image' ? (
        <img
          src={ossThumbUrl(asset.url, 360)}
          alt={asset.name}
          loading="lazy"
          decoding="async"
          className="w-full aspect-square object-cover cursor-zoom-in"
          onClick={handlePreview}
          title="点击放大查看"
        />
      ) : (
        <div
          className="w-full aspect-square bg-[var(--s-bg-secondary)] flex items-center justify-center text-4xl cursor-pointer"
          onClick={onToggle}
        >
          📄
        </div>
      )}

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

      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {asset.type === 'image' && (
          <button
            type="button"
            onClick={handlePreview}
            className="p-1.5 bg-black/60 hover:bg-black/80 text-white rounded"
            aria-label="放大查看"
            title="放大查看"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={handleDownload}
          className="p-1.5 bg-black/60 hover:bg-black/80 text-white rounded"
          aria-label="下载"
          title="下载"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
      </div>

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


export function GenerationCard({
  asset,
  selected,
  onToggle,
  onPreview,
}: {
  asset: GenerationAsset;
  selected: boolean;
  onToggle: () => void;
  onPreview: (url: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    const ext = asset.kind === 'video' ? 'mp4' : 'jpg';
    downloadFile(asset.url, `${asset.id}.${ext}`).catch((err) => toast.error(err?.message || '下载失败'));
  };

  const handlePreview = (e: React.MouseEvent) => {
    e.stopPropagation();
    onPreview(asset.url);
  };

  const handleCopyPrompt = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!asset.prompt) return;
    navigator.clipboard.writeText(asset.prompt);
    toast.success('已复制提示词');
  };

  return (
    <div
      className={`relative border rounded-lg overflow-hidden group transition-colors ${
        selected ? 'border-[var(--s-accent)] ring-2 ring-[var(--s-accent)]/30' : 'border-[var(--s-border-default)]'
      }`}
    >
      {asset.kind === 'image' ? (
        <img
          src={ossThumbUrl(asset.url, 360)}
          alt={asset.prompt || ''}
          loading="lazy"
          decoding="async"
          className="w-full aspect-square object-cover cursor-zoom-in"
          onClick={handlePreview}
          title="点击放大查看"
        />
      ) : (
        <div
          className="relative w-full aspect-square bg-black flex items-center justify-center cursor-pointer"
          onClick={onToggle}
        >
          <video src={asset.url} className="w-full h-full object-cover" muted preload="metadata" />
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-10 h-10 rounded-full bg-white/30 backdrop-blur flex items-center justify-center">
              <span className="text-white text-xl">▶</span>
            </div>
          </div>
        </div>
      )}

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

      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {asset.kind === 'image' && (
          <button
            type="button"
            onClick={handlePreview}
            className="p-1.5 bg-black/60 hover:bg-black/80 text-white rounded"
            aria-label="放大查看"
            title="放大查看"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          type="button"
          onClick={handleDownload}
          className="p-1.5 bg-black/60 hover:bg-black/80 text-white rounded"
          aria-label="下载"
          title="下载"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="p-2 text-xs space-y-1">
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
