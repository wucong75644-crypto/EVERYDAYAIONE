/**
 * 工作区主视图
 *
 * 铺满主内容区（替代 MessageArea + InputArea），提供文件浏览/管理功能。
 * 桌面级交互：单击选中、双击打开、右键菜单、键盘快捷键。
 */

import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useWorkspace } from '../../hooks/useWorkspace';
import { useFileSelection } from '../../hooks/useFileSelection';
import { useRubberBand, rubberBandStyle } from '../../hooks/useRubberBand';
import Modal from '../common/Modal';
import { Button } from '../ui/Button';
import FilePreviewModal, { canPreview } from '../chat/media/FilePreviewModal';
import ImagePreviewModal from '../chat/media/ImagePreviewModal';
import VideoPreviewModal from '../chat/media/VideoPreviewModal';
import FileContextMenu from './FileContextMenu';
// BatchActionBar removed — 多选用轻量文字提示
import WorkspaceHeader from './WorkspaceHeader';
import WorkspaceCategoryTabs from './WorkspaceCategoryTabs';
import WorkspaceFileList from './WorkspaceFileList';
import WorkspaceFileGrid from './WorkspaceFileGrid';
import WorkspaceEmptyState from './WorkspaceEmptyState';
import WorkspaceDropZone from './WorkspaceDropZone';
import { getFullPath } from './WorkspaceFileItem';
import type { WorkspaceFileItem, WorkspaceFile } from '../../services/workspace';
import { downloadWorkspaceZip } from '../../services/workspace';
import { downloadFile } from '../../utils/downloadFile';
import { matchesFilter, canPreviewImage, canPreviewVideo } from '../../utils/fileCategory';
import type { FilePart } from '../../types/message';

interface WorkspaceViewProps {
  onBack: () => void;
  onSendToChat: (file: WorkspaceFile) => void;
}

export default function WorkspaceView({ onBack, onSendToChat }: WorkspaceViewProps) {
  const ws = useWorkspace();
  const selection = useFileSelection();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileAreaRef = useRef<HTMLDivElement>(null);

  // 重命名目标路径（由右键菜单/F2 触发）
  const [renameTarget, setRenameTarget] = useState<string | null>(null);

  // 切换目录时清空选中
  useEffect(() => {
    selection.clear();
    setRenameTarget(null);
  }, [ws.currentPath]); // eslint-disable-line react-hooks/exhaustive-deps

  // 预览弹窗（三类：文档/图片/视频分别独立）
  const [previewFile, setPreviewFile] = useState<FilePart | null>(null);
  const [previewImageIndex, setPreviewImageIndex] = useState<number | null>(null);
  const [previewVideoIndex, setPreviewVideoIndex] = useState<number | null>(null);

  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // 按当前 Tab 过滤后的文件列表（不影响后端拉取，仅 client-side filter）
  // 文件夹仅在「全部」Tab 显示——其他 Tab 下用户聚焦看文件，避免混入容器
  const filteredItems = useMemo(
    () => ws.items.filter((item) => {
      if (item.is_dir) return ws.categoryFilter === 'all';
      return matchesFilter(item, ws.categoryFilter);
    }),
    [ws.items, ws.categoryFilter],
  );

  // 图片/视频上下张所基于的列表 — 仅在当前筛选可见的同类型文件之间循环
  const imageItems = useMemo(
    () => filteredItems.filter((i) => !i.is_dir && canPreviewImage(i) && i.cdn_url),
    [filteredItems],
  );
  const videoItems = useMemo(
    () => filteredItems.filter((i) => !i.is_dir && canPreviewVideo(i) && i.cdn_url),
    [filteredItems],
  );

  // 有序路径列表（供 Shift 范围选 + Ctrl+A 全选用，基于当前可见列表）
  const orderedPaths = useMemo(
    () => filteredItems.map((item) => getFullPath(ws.currentPath, item.name)),
    [filteredItems, ws.currentPath],
  );

  // 单击选中：多选模式下直接 toggle（不需 Ctrl/Shift）；普通模式走原 Ctrl/Shift 修饰逻辑
  const handleSelect = useCallback((path: string, e: React.MouseEvent) => {
    if (ws.multiSelectMode) {
      selection.toggle(path);
    } else {
      selection.handleClick(path, orderedPaths, e);
    }
  }, [orderedPaths, selection, ws.multiSelectMode]);

  // 双击打开 — 按文件分类分发到不同 Modal
  const handleOpen = useCallback((item: WorkspaceFileItem) => {
    if (item.is_dir) {
      ws.navigateTo(getFullPath(ws.currentPath, item.name));
      return;
    }
    // 图片：弹 ImagePreviewModal，索引基于当前可见的图片列表
    if (canPreviewImage(item)) {
      const idx = imageItems.findIndex((i) => i.name === item.name);
      setPreviewImageIndex(idx >= 0 ? idx : 0);
      return;
    }
    // 视频：弹 VideoPreviewModal
    if (canPreviewVideo(item)) {
      const idx = videoItems.findIndex((i) => i.name === item.name);
      setPreviewVideoIndex(idx >= 0 ? idx : 0);
      return;
    }
    // 文档（xlsx/csv/pdf/text）：弹 FilePreviewModal
    if (canPreview(item.name)) {
      setPreviewFile({
        type: 'file',
        url: item.cdn_url || '',
        name: item.name,
        mime_type: item.mime_type || 'application/octet-stream',
        size: item.size,
        workspace_path: getFullPath(ws.currentPath, item.name),
      });
      return;
    }
    // 兜底：触发下载
    if (item.cdn_url) {
      downloadFile(item.cdn_url, item.name);
    }
  }, [ws.currentPath, ws.navigateTo, imageItems, videoItems]);

  const handleSendToChat = useCallback((item: WorkspaceFileItem) => {
    // 多选时插入所有选中的非文件夹项
    const fullPath = getFullPath(ws.currentPath, item.name);
    if (selection.selectedCount > 1 && selection.selectedPaths.has(fullPath)) {
      const selectedItems = ws.items.filter(
        (it) => !it.is_dir && selection.selectedPaths.has(getFullPath(ws.currentPath, it.name)),
      );
      for (const it of selectedItems) {
        onSendToChat({
          name: it.name,
          workspace_path: getFullPath(ws.currentPath, it.name),
          cdn_url: it.cdn_url,
          mime_type: it.mime_type,
          size: it.size,
        });
      }
    } else {
      onSendToChat({
        name: item.name,
        workspace_path: getFullPath(ws.currentPath, item.name),
        cdn_url: item.cdn_url,
        mime_type: item.mime_type,
        size: item.size,
      });
    }
  }, [ws.currentPath, ws.items, onSendToChat, selection.selectedCount, selection.selectedPaths]);

  // 删除（支持批量）
  const handleDelete = useCallback((path: string) => {
    if (selection.selectedCount > 1) {
      // 批量：删除所有选中
      setDeleteTarget(`batch:${selection.selectedCount}`);
    } else {
      setDeleteTarget(path);
    }
  }, [selection.selectedCount]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);

    if (deleteTarget.startsWith('batch:')) {
      // 批量删除
      const paths = Array.from(selection.selectedPaths);
      let successCount = 0;
      for (const path of paths) {
        if (await ws.remove(path)) successCount++;
      }
      toast[successCount === paths.length ? 'success' : 'error'](
        successCount === paths.length ? `已删除 ${successCount} 项` : `删除了 ${successCount}/${paths.length} 项`
      );
      selection.clear();
    } else {
      const success = await ws.remove(deleteTarget);
      toast[success ? 'success' : 'error'](success ? '已删除' : '删除失败');
    }

    setDeleteLoading(false);
    setDeleteTarget(null);
  }, [deleteTarget, ws, selection]);

  const handleUpload = useCallback(async (files: File[]) => {
    const success = await ws.upload(files);
    if (success) toast.success(`已上传 ${files.length} 个文件`);
  }, [ws]);

  // 批量下载：选中 ≥2 项 → ZIP；单文件夹 → ZIP；单文件 → 走原 downloadFile（不打包）
  const handleBatchDownload = useCallback(async (item: WorkspaceFileItem) => {
    const fullPath = getFullPath(ws.currentPath, item.name);
    const isMulti = selection.selectedCount > 1 && selection.selectedPaths.has(fullPath);

    // 1) 多选：打包所有选中
    if (isMulti) {
      const paths = Array.from(selection.selectedPaths);
      const toastId = toast.loading(`正在打包 ${paths.length} 项...`);
      try {
        await downloadWorkspaceZip(paths);
        toast.success(`已下载 ${paths.length} 项`, { id: toastId });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : '下载失败', { id: toastId });
      }
      return;
    }

    // 2) 单文件夹：打包该目录
    if (item.is_dir) {
      const toastId = toast.loading(`正在打包 ${item.name}...`);
      try {
        await downloadWorkspaceZip([fullPath]);
        toast.success('已下载', { id: toastId });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : '下载失败', { id: toastId });
      }
      return;
    }

    // 3) 单文件：走原下载（不打包，保留原扩展名）
    if (item.cdn_url) {
      downloadFile(item.cdn_url, item.name);
    }
  }, [ws.currentPath, selection.selectedCount, selection.selectedPaths]);

  // 多选模式下「下载 (N)」按钮：始终打包所有选中（无锚点 item）
  const handleBatchDownloadAll = useCallback(async () => {
    const paths = Array.from(selection.selectedPaths);
    if (paths.length === 0) return;
    const toastId = toast.loading(`正在打包 ${paths.length} 项...`);
    try {
      await downloadWorkspaceZip(paths);
      toast.success(`已下载 ${paths.length} 项`, { id: toastId });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '下载失败', { id: toastId });
    }
  }, [selection.selectedPaths]);

  // 切换多选模式：开启时不动选中；关闭时清空选中
  const handleToggleMultiSelect = useCallback(() => {
    const next = !ws.multiSelectMode;
    ws.setMultiSelectMode(next);
    if (!next) selection.clear();
  }, [ws, selection]);

  // additive（Ctrl/Cmd/Shift+拖）的基线快照：拖拽开始时 snapshot 当前选中
  const additiveBaselineRef = useRef<string[]>([]);

  // mousemove 实时调用：additive 模式合并 baseline，普通模式直接覆盖
  const handleRubberSelect = useCallback((paths: string[], additive: boolean) => {
    if (!additive) {
      selection.selectAll(paths);
      return;
    }
    const merged = new Set<string>(additiveBaselineRef.current);
    for (const p of paths) merged.add(p);
    selection.selectAll(Array.from(merged));
  }, [selection]);

  const rubberBand = useRubberBand({
    containerRef: fileAreaRef,
    onSelectionChange: handleRubberSelect,
    onDragStart: useCallback(() => {
      additiveBaselineRef.current = Array.from(selection.selectedPaths);
    }, [selection.selectedPaths]),
    enabled: !ws.multiSelectMode,
  });

  // 点击空白区域清空选中（grid 间隙、列表行间、容器内的空白都算）
  const handleBlankClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    // 点击落在文件卡片内 → 不算空白
    if (target.closest('[data-workspace-path]')) return;
    // 点击落在交互元素（按钮、表头排序、输入框等）→ 不算空白
    if (target.closest('button, input, textarea, [role="menuitem"], [role="tab"]')) return;
    selection.clear();
  }, [selection]);

  // 键盘快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 重命名/弹窗/输入框中不拦截（含三类预览 Modal）
      if (renameTarget || deleteTarget || previewFile || previewImageIndex !== null || previewVideoIndex !== null) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;

      // Ctrl/Cmd + A → 全选
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        e.preventDefault();
        selection.selectAll(orderedPaths);
        return;
      }
      // Escape → 清空选中 +（如多选模式）退出多选
      if (e.key === 'Escape') {
        selection.clear();
        if (ws.multiSelectMode) ws.setMultiSelectMode(false);
        return;
      }
      // 以下需要有选中项
      if (!selection.hasSelection) return;

      // Delete / Backspace → 删除
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        if (selection.selectedCount > 1) {
          setDeleteTarget(`batch:${selection.selectedCount}`);
        } else {
          setDeleteTarget(Array.from(selection.selectedPaths)[0]);
        }
        return;
      }
      // F2 → 重命名（仅单选）
      if (e.key === 'F2' && selection.selectedCount === 1) {
        e.preventDefault();
        setRenameTarget(Array.from(selection.selectedPaths)[0]);
        return;
      }
      // Enter → 打开（仅单选）
      if (e.key === 'Enter' && selection.selectedCount === 1) {
        e.preventDefault();
        const path = Array.from(selection.selectedPaths)[0];
        const item = ws.items.find((i) => getFullPath(ws.currentPath, i.name) === path);
        if (item) handleOpen(item);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [renameTarget, deleteTarget, previewFile, previewImageIndex, previewVideoIndex, selection, orderedPaths, ws.items, ws.currentPath, handleOpen]);

  // 删除弹窗显示名称
  const deleteDisplayName = deleteTarget?.startsWith('batch:')
    ? `${deleteTarget.split(':')[1]} 个文件`
    : deleteTarget?.split('/').pop();

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[var(--s-surface-base)]" tabIndex={-1}>
      <WorkspaceHeader
        breadcrumbs={ws.breadcrumbs}
        viewMode={ws.viewMode}
        onBack={onBack}
        onNavigate={ws.navigateTo}
        onViewModeChange={ws.setViewMode}
        onUpload={handleUpload}
        onMkdir={ws.mkdir}
      />

      <WorkspaceCategoryTabs
        value={ws.categoryFilter}
        onChange={ws.setCategoryFilter}
        multiSelectMode={ws.multiSelectMode}
        onToggleMultiSelect={handleToggleMultiSelect}
        selectedCount={selection.selectedCount}
        onBatchDownload={handleBatchDownloadAll}
      />

      {/* 多选提示已移除 — 选中态通过文件卡片高亮体现 */}

      {/* 错误提示 */}
      {ws.error && (
        <div className="mx-4 mt-2 px-3 py-2 text-sm bg-[var(--s-error-soft)] text-[var(--s-error)] rounded-[var(--s-radius-control)] flex items-center justify-between">
          <span>{ws.error}</span>
          <button type="button" onClick={ws.clearError} className="text-[var(--s-error)] hover:underline text-xs shrink-0 ml-2">关闭</button>
        </div>
      )}

      {/* 文件区域 — 空白处右键菜单 */}
      <WorkspaceDropZone onDrop={handleUpload}>
        <FileContextMenu
          type="blank"
          blankProps={{
            onNewFolder: () => ws.mkdir('新建文件夹'),
            onUpload: () => fileInputRef.current?.click(),
          }}
        >
          <div ref={fileAreaRef} className="relative flex-1 overflow-y-auto select-none" onClick={handleBlankClick}>
            {/* 拖拽框选矩形（rubber-band） */}
            {rubberBand.rect && <div style={rubberBandStyle(rubberBand.rect)} />}
            {ws.loading && ws.items.length === 0 ? (
              <div className="flex-1 flex items-center justify-center h-full">
                <Loader2 className="w-8 h-8 text-[var(--s-text-tertiary)] animate-spin" />
              </div>
            ) : ws.items.length === 0 ? (
              <WorkspaceEmptyState />
            ) : filteredItems.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center h-full px-6 text-center">
                <div className="text-4xl mb-3" aria-hidden>📂</div>
                <div className="text-sm text-[var(--s-text-secondary)]">该分类下暂无文件</div>
              </div>
            ) : ws.viewMode === 'list' ? (
              <div className="px-1">
                <WorkspaceFileList
                  items={filteredItems}
                  currentPath={ws.currentPath}
                  selectedPaths={selection.selectedPaths}
                  renameTarget={renameTarget}
                  sortField={ws.sortField}
                  sortOrder={ws.sortOrder}
                  onToggleSort={ws.toggleSort}
                  onSelect={handleSelect}
                  onOpen={handleOpen}
                  onRename={ws.rename}
                  onRenameEnd={() => setRenameTarget(null)}
                  onDelete={handleDelete}
                  onSendToChat={handleSendToChat}
                  onStartRename={setRenameTarget}
                  onMove={ws.move}
                  onBatchDownload={handleBatchDownload}
                  multiSelectMode={ws.multiSelectMode}
                />
              </div>
            ) : (
              <WorkspaceFileGrid
                items={filteredItems}
                currentPath={ws.currentPath}
                selectedPaths={selection.selectedPaths}
                renameTarget={renameTarget}
                onSelect={handleSelect}
                onOpen={handleOpen}
                onRename={ws.rename}
                onRenameEnd={() => setRenameTarget(null)}
                onDelete={handleDelete}
                onSendToChat={handleSendToChat}
                onStartRename={setRenameTarget}
                onMove={ws.move}
                onBatchDownload={handleBatchDownload}
                multiSelectMode={ws.multiSelectMode}
              />
            )}
          </div>
        </FileContextMenu>
      </WorkspaceDropZone>

      {/* 隐藏文件输入（空白区域右键"上传文件"用） */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        onChange={(e) => {
          if (e.target.files) handleUpload(Array.from(e.target.files));
          e.target.value = '';
        }}
        className="hidden"
      />

      {/* 删除确认弹窗 */}
      <Modal isOpen={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="确认删除" maxWidth="sm">
        <p className="text-sm text-[var(--s-text-secondary)] mb-4">
          确定删除 <span className="font-medium text-[var(--s-text-primary)]">{deleteDisplayName}</span> 吗？此操作无法撤销。
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button variant="danger" size="sm" loading={deleteLoading} onClick={handleDeleteConfirm}>删除</Button>
        </div>
      </Modal>

      {/* 文档预览（xlsx/csv/pdf/text）*/}
      {previewFile && <FilePreviewModal file={previewFile} onClose={() => setPreviewFile(null)} />}

      {/* 图片预览（仅在筛选可见的图片间切换）*/}
      {previewImageIndex !== null && imageItems[previewImageIndex] && (
        <ImagePreviewModal
          imageUrl={imageItems[previewImageIndex].cdn_url || ''}
          filename={imageItems[previewImageIndex].name}
          onClose={() => setPreviewImageIndex(null)}
          onPrev={() => setPreviewImageIndex((i) => (i !== null && i > 0 ? i - 1 : i))}
          onNext={() => setPreviewImageIndex((i) => (i !== null && i < imageItems.length - 1 ? i + 1 : i))}
          hasPrev={previewImageIndex > 0}
          hasNext={previewImageIndex < imageItems.length - 1}
          allImages={imageItems.map((i) => i.cdn_url || '')}
          currentIndex={previewImageIndex}
          onSelectImage={(idx) => setPreviewImageIndex(idx)}
        />
      )}

      {/* 视频预览（仅在筛选可见的视频间切换）*/}
      {previewVideoIndex !== null && videoItems[previewVideoIndex] && (
        <VideoPreviewModal
          videoUrl={videoItems[previewVideoIndex].cdn_url || ''}
          filename={videoItems[previewVideoIndex].name}
          onClose={() => setPreviewVideoIndex(null)}
          onPrev={() => setPreviewVideoIndex((i) => (i !== null && i > 0 ? i - 1 : i))}
          onNext={() => setPreviewVideoIndex((i) => (i !== null && i < videoItems.length - 1 ? i + 1 : i))}
          hasPrev={previewVideoIndex > 0}
          hasNext={previewVideoIndex < videoItems.length - 1}
        />
      )}
    </div>
  );
}
