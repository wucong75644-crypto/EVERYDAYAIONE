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
import { usePreview } from '../../preview/usePreview';
import PreviewHost from '../../preview/PreviewHost';
import { fromWorkspaceItem } from '../../preview/toPreviewItem';
import { resolveAdapter } from '../../preview/registry';
import type { PreviewItem } from '../../preview/types';
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
import { categorize, matchesFilter } from '../../utils/fileCategory';
import { toOriginalImageUrl } from '../../utils/imageUrlRules';

interface WorkspaceViewProps {
  onBack: () => void;
  onSendToChat: (file: WorkspaceFile) => void;
}

export default function WorkspaceView({ onBack, onSendToChat }: WorkspaceViewProps) {
  const ws = useWorkspace();
  const selection = useFileSelection();
  const preview = usePreview();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileAreaRef = useRef<HTMLDivElement>(null);

  // 重命名目标路径（由右键菜单/F2 触发）
  const [renameTarget, setRenameTarget] = useState<string | null>(null);

  // 切换目录时清空选中
  useEffect(() => {
    selection.clear();
    setRenameTarget(null);
  }, [ws.currentPath]); // eslint-disable-line react-hooks/exhaustive-deps

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
    () => filteredItems.filter((i) => !i.is_dir && categorize(i) === 'image' && i.cdn_url),
    [filteredItems],
  );
  const videoItems = useMemo(
    () => filteredItems.filter((i) => !i.is_dir && categorize(i) === 'video' && i.cdn_url),
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

  // 双击打开 — 通过 registry 选 adapter，统一交给 PreviewHost 渲染
  const handleOpen = useCallback((item: WorkspaceFileItem) => {
    if (item.is_dir) {
      ws.navigateTo(getFullPath(ws.currentPath, item.name));
      return;
    }
    const fullPath = getFullPath(ws.currentPath, item.name);
    const ctx = fromWorkspaceItem(item, fullPath);
    const adapter = resolveAdapter(ctx);
    // 图片/视频走上下张：兄弟列表用同分类的当前可见项
    let siblings: PreviewItem[] = [ctx];
    let index = 0;
    if (adapter?.id === 'image') {
      siblings = imageItems.map((i) => fromWorkspaceItem(i, getFullPath(ws.currentPath, i.name)));
      const found = imageItems.findIndex((i) => i.name === item.name);
      index = found >= 0 ? found : 0;
    } else if (adapter?.id === 'video') {
      siblings = videoItems.map((i) => fromWorkspaceItem(i, getFullPath(ws.currentPath, i.name)));
      const found = videoItems.findIndex((i) => i.name === item.name);
      index = found >= 0 ? found : 0;
    }
    preview.open(siblings, index);
  }, [ws.currentPath, ws.navigateTo, imageItems, videoItems, preview]);

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
          cdn_url: it.cdn_url ? toOriginalImageUrl(it.cdn_url) : null,
          mime_type: it.mime_type,
          size: it.size,
        });
      }
    } else {
      onSendToChat({
        name: item.name,
        workspace_path: getFullPath(ws.currentPath, item.name),
        cdn_url: item.cdn_url ? toOriginalImageUrl(item.cdn_url) : null,
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
      downloadFile(toOriginalImageUrl(item.cdn_url), item.name);
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
    // 纯点击空白 → 清空选中（统一收归 hook 管理，避免 React onClick 事件序列竞态）
    onEmptyClick: useCallback(() => {
      selection.clear();
    }, [selection]),
    enabled: !ws.multiSelectMode,
  });

  // 键盘快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 重命名/弹窗/输入框中不拦截（含预览 Modal 打开时）
      if (renameTarget || deleteTarget || preview.isOpen) return;
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
  }, [renameTarget, deleteTarget, preview.isOpen, selection, orderedPaths, ws.items, ws.currentPath, handleOpen]);

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
          <div ref={fileAreaRef} className="relative flex-1 overflow-y-auto select-none">
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

      {/* 统一预览入口（registry 自动按文件类型分发到对应 adapter）*/}
      <PreviewHost
        state={preview.state}
        onClose={preview.close}
        onIndexChange={preview.setIndex}
      />
    </div>
  );
}
