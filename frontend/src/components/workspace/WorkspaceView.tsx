/**
 * 工作区主视图
 *
 * 铺满主内容区（替代 MessageArea + InputArea），提供文件浏览/管理功能。
 * 严格使用设计系统 V3 token + ui/ 组件。
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useWorkspace } from '../../hooks/useWorkspace';
import Modal from '../common/Modal';
import { Button } from '../ui/Button';
import FilePreviewModal, { canPreview } from '../chat/media/FilePreviewModal';
import WorkspaceHeader from './WorkspaceHeader';
import WorkspaceFileList from './WorkspaceFileList';
import WorkspaceFileGrid from './WorkspaceFileGrid';
import WorkspaceEmptyState from './WorkspaceEmptyState';
import WorkspaceDropZone from './WorkspaceDropZone';
import type { WorkspaceFileItem, WorkspaceFile } from '../../services/workspace';
import type { FilePart } from '../../types/message';

interface WorkspaceViewProps {
  /** 返回对话视图 */
  onBack: () => void;
  /** 将文件插入到聊天 */
  onSendToChat: (file: WorkspaceFile) => void;
  /** 从外部触发的待上传文件（如上传菜单） */
  pendingUploadFiles?: File[];
  /** 待上传文件已消费，清空队列 */
  onPendingUploadConsumed?: () => void;
}

export default function WorkspaceView({ onBack, onSendToChat, pendingUploadFiles, onPendingUploadConsumed }: WorkspaceViewProps) {
  const ws = useWorkspace();
  const pendingConsumedRef = useRef(false);

  // 接收从外部传入的文件并触发上传
  useEffect(() => {
    if (pendingUploadFiles && pendingUploadFiles.length > 0 && !pendingConsumedRef.current) {
      pendingConsumedRef.current = true;
      (async () => {
        const success = await ws.upload(pendingUploadFiles);
        if (success) {
          toast.success(`已上传 ${pendingUploadFiles.length} 个文件`);
        }
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

  const handlePreview = useCallback((item: WorkspaceFileItem) => {
    if (!item.cdn_url) {
      toast.error('文件暂不支持预���');
      return;
    }
    if (canPreview(item.name)) {
      setPreviewFile({
        type: 'file',
        url: item.cdn_url,
        name: item.name,
        mime_type: item.mime_type || 'application/octet-stream',
        size: item.size,
      });
    } else if (item.cdn_url) {
      // 不支持预览 → 下载
      const link = document.createElement('a');
      link.href = item.cdn_url;
      link.download = item.name;
      link.click();
    }
  }, []);

  const handleSendToChat = useCallback((item: WorkspaceFileItem) => {
    const fullPath = ws.currentPath === '.' ? item.name : `${ws.currentPath}/${item.name}`;
    onSendToChat({
      name: item.name,
      workspace_path: fullPath,
      cdn_url: item.cdn_url,
      mime_type: item.mime_type,
      size: item.size,
    });
  }, [ws.currentPath, onSendToChat]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    const success = await ws.remove(deleteTarget);
    setDeleteLoading(false);
    toast[success ? 'success' : 'error'](success ? '已删除' : '删除失败');
    setDeleteTarget(null);
  }, [deleteTarget, ws.remove]);

  const handleUpload = useCallback(async (files: File[]) => {
    const success = await ws.upload(files);
    if (success) {
      toast.success(`已上传 ${files.length} 个文件`);
    }
  }, [ws.upload]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[var(--s-surface-base)]">
      {/* 顶部栏 */}
      <WorkspaceHeader
        breadcrumbs={ws.breadcrumbs}
        viewMode={ws.viewMode}
        onBack={onBack}
        onNavigate={ws.navigateTo}
        onViewModeChange={ws.setViewMode}
        onUpload={handleUpload}
        onMkdir={ws.mkdir}
      />

      {/* 错误提示 */}
      {ws.error && (
        <div className="mx-4 mt-2 px-3 py-2 text-sm bg-[var(--s-error-soft)] text-[var(--s-error)] rounded-[var(--s-radius-control)] flex items-center justify-between">
          <span>{ws.error}</span>
          <button
            type="button"
            onClick={ws.clearError}
            className="text-[var(--s-error)] hover:underline text-xs shrink-0 ml-2"
          >
            关闭
          </button>
        </div>
      )}

      {/* 文件区域 */}
      <WorkspaceDropZone onDrop={handleUpload}>
        {ws.loading && ws.items.length === 0 ? (
          /* 骨架屏 / 加载态 */
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-8 h-8 text-[var(--s-text-tertiary)] animate-spin" />
          </div>
        ) : ws.items.length === 0 ? (
          <WorkspaceEmptyState />
        ) : ws.viewMode === 'list' ? (
          <div className="flex-1 overflow-y-auto px-1">
            <WorkspaceFileList
              items={ws.items}
              currentPath={ws.currentPath}
              onNavigate={ws.navigateTo}
              onRename={ws.rename}
              onDelete={setDeleteTarget}
              onPreview={handlePreview}
              onSendToChat={handleSendToChat}
            />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            <WorkspaceFileGrid
              items={ws.items}
              currentPath={ws.currentPath}
              onNavigate={ws.navigateTo}
              onRename={ws.rename}
              onDelete={setDeleteTarget}
              onPreview={handlePreview}
              onSendToChat={handleSendToChat}
            />
          </div>
        )}
      </WorkspaceDropZone>

      {/* 删除确认弹窗 */}
      <Modal
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="确认删除"
        maxWidth="sm"
      >
        <p className="text-sm text-[var(--s-text-secondary)] mb-4">
          确定删除 <span className="font-medium text-[var(--s-text-primary)]">{deleteTarget?.split('/').pop()}</span> 吗？此操作无法撤销。
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(null)}>
            取消
          </Button>
          <Button variant="danger" size="sm" loading={deleteLoading} onClick={handleDeleteConfirm}>
            删除
          </Button>
        </div>
      </Modal>

      {/* 文件预览弹窗 */}
      {previewFile && (
        <FilePreviewModal
          file={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  );
}
