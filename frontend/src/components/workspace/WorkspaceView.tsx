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
import Modal from '../common/Modal';
import { Button } from '../ui/Button';
import FilePreviewModal, { canPreview } from '../chat/media/FilePreviewModal';
import FileContextMenu from './FileContextMenu';
// BatchActionBar removed — 多选用轻量文字提示
import WorkspaceHeader from './WorkspaceHeader';
import WorkspaceFileList from './WorkspaceFileList';
import WorkspaceFileGrid from './WorkspaceFileGrid';
import WorkspaceEmptyState from './WorkspaceEmptyState';
import WorkspaceDropZone from './WorkspaceDropZone';
import { getFullPath } from './WorkspaceFileItem';
import type { WorkspaceFileItem, WorkspaceFile } from '../../services/workspace';
import { downloadFile } from '../../utils/downloadFile';
import type { FilePart } from '../../types/message';

interface WorkspaceViewProps {
  onBack: () => void;
  onSendToChat: (file: WorkspaceFile) => void;
  pendingUploadFiles?: File[];
  onPendingUploadConsumed?: () => void;
}

export default function WorkspaceView({ onBack, onSendToChat, pendingUploadFiles, onPendingUploadConsumed }: WorkspaceViewProps) {
  const ws = useWorkspace();
  const selection = useFileSelection();
  const pendingConsumedRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 重命名目标路径（由右键菜单/F2 触发）
  const [renameTarget, setRenameTarget] = useState<string | null>(null);

  // 切换目录时清空选中
  useEffect(() => {
    selection.clear();
    setRenameTarget(null);
  }, [ws.currentPath]); // eslint-disable-line react-hooks/exhaustive-deps

  // 接收外部待上传文件
  useEffect(() => {
    if (pendingUploadFiles && pendingUploadFiles.length > 0 && !pendingConsumedRef.current) {
      pendingConsumedRef.current = true;
      (async () => {
        const success = await ws.upload(pendingUploadFiles);
        if (success) toast.success(`已上传 ${pendingUploadFiles.length} 个文件`);
        onPendingUploadConsumed?.();
        pendingConsumedRef.current = false;
      })();
    }
  }, [pendingUploadFiles]); // eslint-disable-line react-hooks/exhaustive-deps

  // 预览弹窗
  const [previewFile, setPreviewFile] = useState<FilePart | null>(null);

  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // 有序路径列表（供 Shift 范围选用）
  const orderedPaths = useMemo(
    () => ws.items.map((item) => getFullPath(ws.currentPath, item.name)),
    [ws.items, ws.currentPath],
  );

  // 单击选中（处理 Ctrl/Shift）
  const handleSelect = useCallback((path: string, e: React.MouseEvent) => {
    selection.handleClick(path, orderedPaths, e);
  }, [orderedPaths, selection]);

  // 双击打开
  const handleOpen = useCallback((item: WorkspaceFileItem) => {
    if (item.is_dir) {
      ws.navigateTo(getFullPath(ws.currentPath, item.name));
    } else if (canPreview(item.name)) {
      setPreviewFile({
        type: 'file',
        url: item.cdn_url || '',
        name: item.name,
        mime_type: item.mime_type || 'application/octet-stream',
        size: item.size,
        workspace_path: getFullPath(ws.currentPath, item.name),
      });
    } else if (item.cdn_url) {
      downloadFile(item.cdn_url, item.name);
    }
  }, [ws.currentPath, ws.navigateTo]);

  const handleSendToChat = useCallback((item: WorkspaceFileItem) => {
    onSendToChat({
      name: item.name,
      workspace_path: getFullPath(ws.currentPath, item.name),
      cdn_url: item.cdn_url,
      mime_type: item.mime_type,
      size: item.size,
    });
  }, [ws.currentPath, onSendToChat]);

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

  // 点击空白区域清空选中
  const handleBlankClick = useCallback((e: React.MouseEvent) => {
    // 只有点击到容器本身（非子元素冒泡）时清空
    if (e.target === e.currentTarget) {
      selection.clear();
    }
  }, [selection]);

  // 键盘快捷键
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // 重命名/弹窗/输入框中不拦截
      if (renameTarget || deleteTarget || previewFile) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;

      // Ctrl/Cmd + A → 全选
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        e.preventDefault();
        selection.selectAll(orderedPaths);
        return;
      }
      // Escape → 清空选中
      if (e.key === 'Escape') {
        selection.clear();
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
  }, [renameTarget, deleteTarget, previewFile, selection, orderedPaths, ws.items, ws.currentPath, handleOpen]);

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
          <div className="flex-1 overflow-y-auto select-none" onClick={handleBlankClick}>
            {ws.loading && ws.items.length === 0 ? (
              <div className="flex-1 flex items-center justify-center h-full">
                <Loader2 className="w-8 h-8 text-[var(--s-text-tertiary)] animate-spin" />
              </div>
            ) : ws.items.length === 0 ? (
              <WorkspaceEmptyState />
            ) : ws.viewMode === 'list' ? (
              <div className="px-1">
                <WorkspaceFileList
                  items={ws.items}
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
                />
              </div>
            ) : (
              <WorkspaceFileGrid
                items={ws.items}
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

      {/* 文件预览弹窗 */}
      {previewFile && <FilePreviewModal file={previewFile} onClose={() => setPreviewFile(null)} />}
    </div>
  );
}
