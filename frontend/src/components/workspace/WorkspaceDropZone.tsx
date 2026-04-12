/**
 * 工作区拖拽上传遮罩
 */

import { useState, useCallback, useRef, type DragEvent, type ReactNode } from 'react';
import { Upload } from 'lucide-react';
import { m, AnimatePresence } from 'framer-motion';
import { WORKSPACE_ALLOWED_EXTENSIONS, WORKSPACE_MAX_FILE_SIZE } from '../../services/workspace';

interface WorkspaceDropZoneProps {
  children: ReactNode;
  onDrop: (files: File[]) => void;
}

export default function WorkspaceDropZone({ children, onDrop }: WorkspaceDropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);

  const handleDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    dragCounterRef.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0;
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    dragCounterRef.current = 0;

    const fileList = e.dataTransfer.files;
    if (!fileList.length) return;

    const validFiles: File[] = [];
    for (const file of Array.from(fileList)) {
      const ext = file.name.split('.').pop()?.toLowerCase() || '';
      if (!WORKSPACE_ALLOWED_EXTENSIONS.has(ext)) continue;
      if (file.size > WORKSPACE_MAX_FILE_SIZE) continue;
      validFiles.push(file);
    }

    if (validFiles.length > 0) {
      onDrop(validFiles);
    } else if (fileList.length > 0) {
      // 所有文件都被过滤了 — 动态导入 toast 提示
      import('react-hot-toast').then(({ default: toast }) => {
        toast.error('不支持的文件类型或文件过大（上限 50MB）');
      });
    }
  }, [onDrop]);

  return (
    <div
      className="relative flex-1 flex flex-col min-h-0"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {children}

      <AnimatePresence>
        {isDragging && (
          <m.div
            className="absolute inset-0 z-20 flex items-center justify-center bg-[var(--s-accent-soft)] rounded-[var(--s-radius-card)] border-2 border-dashed border-[var(--s-accent)]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            <div className="text-center">
              <Upload className="w-12 h-12 text-[var(--s-accent)] mx-auto mb-2" />
              <p className="text-[var(--s-accent)] font-medium">
                拖放文件到这里上传
              </p>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
