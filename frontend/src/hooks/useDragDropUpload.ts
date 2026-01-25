/**
 * 拖拽上传自定义Hook
 *
 * 处理拖放和粘贴上传图片的事件逻辑
 */

import { useState, useEffect, type RefObject } from 'react';

interface UseDragDropUploadProps {
  dropZoneRef: RefObject<HTMLElement | null>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onImageDrop: (files: FileList, maxImages?: number, maxFileSize?: number) => void;
  onImagePaste: (e: ClipboardEvent, maxImages?: number, maxFileSize?: number) => void;
  maxImages?: number;
  maxFileSize?: number;
}

export function useDragDropUpload({
  dropZoneRef,
  textareaRef,
  onImageDrop,
  onImagePaste,
  maxImages,
  maxFileSize,
}: UseDragDropUploadProps) {
  const [isDragging, setIsDragging] = useState(false);

  // 拖拽上传事件处理
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

      const files = e.dataTransfer?.files;
      if (files && files.length > 0) {
        onImageDrop(files, maxImages, maxFileSize);
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
  }, [dropZoneRef, onImageDrop, maxImages, maxFileSize]);

  // 粘贴上传事件处理
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      if (document.activeElement === textareaRef.current) {
        onImagePaste(e, maxImages, maxFileSize);
      }
    };

    document.addEventListener('paste', handlePaste);
    return () => document.removeEventListener('paste', handlePaste);
  }, [textareaRef, onImagePaste, maxImages, maxFileSize]);

  return { isDragging };
}
