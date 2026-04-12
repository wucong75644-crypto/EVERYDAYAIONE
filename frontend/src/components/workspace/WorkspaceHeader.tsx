/**
 * 工作区顶部栏
 *
 * 包含：返回按钮、面包屑、视图切换、新建文件夹、上传文件
 */

import { useRef, useState, useCallback } from 'react';
import { ArrowLeft, FolderPlus, Upload, LayoutList, LayoutGrid } from 'lucide-react';
import { Button } from '../ui/Button';
import { cn } from '../../utils/cn';
import WorkspaceBreadcrumb from './WorkspaceBreadcrumb';
import type { ViewMode } from '../../hooks/useWorkspace';
import { WORKSPACE_ALLOWED_EXTENSIONS } from '../../services/workspace';

interface WorkspaceHeaderProps {
  breadcrumbs: { label: string; path: string }[];
  viewMode: ViewMode;
  onBack: () => void;
  onNavigate: (path: string) => void;
  onViewModeChange: (mode: ViewMode) => void;
  onUpload: (files: File[]) => void;
  onMkdir: (name: string) => Promise<boolean>;
}

export default function WorkspaceHeader({
  breadcrumbs,
  viewMode,
  onBack,
  onNavigate,
  onViewModeChange,
  onUpload,
  onMkdir,
}: WorkspaceHeaderProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showMkdirInput, setShowMkdirInput] = useState(false);
  const [folderName, setFolderName] = useState('');
  const mkdirSubmittingRef = useRef(false);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      onUpload(Array.from(files));
    }
    e.target.value = '';
  };

  const handleMkdirSubmit = useCallback(async () => {
    if (mkdirSubmittingRef.current) return; // 防止 blur+Enter 双提交
    const name = folderName.trim();
    if (!name) {
      setShowMkdirInput(false);
      return;
    }
    mkdirSubmittingRef.current = true;
    try {
      const success = await onMkdir(name);
      if (success) {
        setShowMkdirInput(false);
        setFolderName('');
      }
    } finally {
      mkdirSubmittingRef.current = false;
    }
  }, [folderName, onMkdir]);

  const acceptExtensions = Array.from(WORKSPACE_ALLOWED_EXTENSIONS)
    .map((ext) => `.${ext}`)
    .join(',');

  return (
    <header className="flex flex-col gap-2 px-4 py-3 border-b border-[var(--s-border-default)] bg-[var(--s-surface-overlay)] shrink-0">
      {/* 第一行：返回 + 面包屑 + 工具按钮 */}
      <div className="flex items-center gap-2">
        {/* 返回对话 */}
        <Button variant="ghost" size="sm" onClick={onBack} className="shrink-0 !px-2">
          <ArrowLeft className="w-4 h-4" />
          <span className="text-sm">返回对话</span>
        </Button>

        {/* 分隔线 */}
        <div className="w-px h-5 bg-[var(--s-border-subtle)]" />

        {/* 面包屑 */}
        <div className="flex-1 min-w-0">
          <WorkspaceBreadcrumb items={breadcrumbs} onNavigate={onNavigate} />
        </div>

        {/* 视图切换 */}
        <div className="flex items-center bg-[var(--s-surface-sunken)] rounded-[var(--s-radius-control)] p-0.5">
          <button
            type="button"
            onClick={() => onViewModeChange('list')}
            className={cn(
              'p-1.5 rounded transition-colors',
              viewMode === 'list'
                ? 'bg-[var(--s-surface-raised)] text-[var(--s-text-primary)] shadow-sm'
                : 'text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)]',
            )}
            aria-label="列表视图"
          >
            <LayoutList className="w-4 h-4" />
          </button>
          <button
            type="button"
            onClick={() => onViewModeChange('grid')}
            className={cn(
              'p-1.5 rounded transition-colors',
              viewMode === 'grid'
                ? 'bg-[var(--s-surface-raised)] text-[var(--s-text-primary)] shadow-sm'
                : 'text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)]',
            )}
            aria-label="图标视图"
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
        </div>

        {/* 新建文件夹 */}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { setShowMkdirInput(true); setFolderName(''); }}
          className="shrink-0"
        >
          <FolderPlus className="w-4 h-4" />
        </Button>

        {/* 上传 */}
        <Button variant="accent" size="sm" onClick={() => fileInputRef.current?.click()} className="shrink-0">
          <Upload className="w-4 h-4" />
          <span>上传</span>
        </Button>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={acceptExtensions}
          onChange={handleFileSelect}
          className="hidden"
          aria-label="选择文件上传"
        />
      </div>

      {/* 新建文件夹输入框（条件显示） */}
      {showMkdirInput && (
        <div className="flex items-center gap-2">
          <FolderPlus className="w-4 h-4 text-[var(--s-text-tertiary)] shrink-0" />
          <input
            autoFocus
            value={folderName}
            onChange={(e) => setFolderName(e.target.value)}
            onBlur={handleMkdirSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleMkdirSubmit();
              if (e.key === 'Escape') { setShowMkdirInput(false); setFolderName(''); }
            }}
            placeholder="文件夹名称..."
            className="flex-1 px-2 py-1 text-sm bg-[var(--s-surface-base)] border border-[var(--s-border-focus)] rounded-[var(--s-radius-control)] outline-none text-[var(--s-text-primary)] placeholder:text-[var(--s-text-tertiary)]"
          />
        </div>
      )}
    </header>
  );
}
