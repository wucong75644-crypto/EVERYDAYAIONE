/**
 * 拖拽 / 粘贴上传 Hook
 *
 * 统一两条事件入口：dropZone 内拖放 + textarea 内粘贴 → 调用 onFiles(File[])。
 * 不过滤 mime，由上层 handleUnifiedFiles 按 image/* vs 其他分流到对应 hook。
 *
 * 修复历史：旧版只接受 image/* mime，导致 PDF/Excel/Word 粘贴静默丢失、拖拽报错。
 */

import { useState, useEffect, type RefObject } from 'react';

interface UseDragDropUploadProps {
  dropZoneRef: RefObject<HTMLElement | null>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  /** 统一文件回调：拖放和粘贴都走这一个出口 */
  onFiles: (files: File[]) => void;
}

export function useDragDropUpload({
  dropZoneRef,
  textareaRef,
  onFiles,
}: UseDragDropUploadProps) {
  const [isDragging, setIsDragging] = useState(false);

  // 拖放：从 DragEvent 提取所有 files
  useEffect(() => {
    const dropZone = dropZoneRef.current;
    if (!dropZone) return;

    const handleDragEnter = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.dataTransfer?.types.includes('Files')) {
        setIsDragging(true);
      }
    };

    const handleDragOver = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };

    const handleDragLeave = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.target === dropZone) {
        setIsDragging(false);
      }
    };

    const handleDrop = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);

      const fileList = e.dataTransfer?.files;
      if (fileList && fileList.length > 0) {
        onFiles(Array.from(fileList));
      }
    };

    dropZone.addEventListener('dragenter', handleDragEnter);
    dropZone.addEventListener('dragover', handleDragOver);
    dropZone.addEventListener('dragleave', handleDragLeave);
    dropZone.addEventListener('drop', handleDrop);

    return () => {
      dropZone.removeEventListener('dragenter', handleDragEnter);
      dropZone.removeEventListener('dragover', handleDragOver);
      dropZone.removeEventListener('dragleave', handleDragLeave);
      dropZone.removeEventListener('drop', handleDrop);
    };
  }, [dropZoneRef, onFiles]);

  // 粘贴：从 ClipboardEvent.items 提取所有 kind=='file' 的项目（图片+文档+任意文件）
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      if (document.activeElement !== textareaRef.current) return;

      const items = e.clipboardData?.items;
      if (!items || items.length === 0) return;

      const files: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        // 关键修复：检查 kind==='file' 而非 mime 是否含 'image'，
        // 这样 PDF/Excel/Word 等任何文件类型的粘贴都能捕获
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) files.push(f);
        }
      }

      if (files.length > 0) {
        e.preventDefault();
        onFiles(files);
      }
    };

    document.addEventListener('paste', handlePaste);
    return () => document.removeEventListener('paste', handlePaste);
  }, [textareaRef, onFiles]);

  return { isDragging };
}
