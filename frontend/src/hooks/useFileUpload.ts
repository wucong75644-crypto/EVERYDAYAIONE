/**
 * 文件上传 Hook（通用文档/数据/文本文件）
 *
 * 处理非图片文件的选择、校验、上传、预览和删除。
 * 与后端 _WORKSPACE_ALLOWED_EXTENSIONS 对齐：PDF/Office/数据/文本/代码 等。
 * 图片走 useImageUpload。
 */

import { useState, type ChangeEvent } from 'react';
import { uploadFile, type UploadFileResponse } from '../services/fileUpload';
import { logger } from '../utils/logger';

// 通用非图片文件扩展名（与后端 _WORKSPACE_ALLOWED_EXTENSIONS 对齐，剔除图片）
const ALLOWED_EXTS = new Set([
  'txt', 'csv', 'json', 'yaml', 'yml', 'xml', 'md', 'log', 'tsv',
  'py', 'js', 'ts', 'html', 'css', 'sql',
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'zip',
]);

function getExt(name: string): string {
  return name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
}

const DEFAULT_MAX_SIZE = 100 * 1024 * 1024; // 100MB（与后端 _WORKSPACE_MAX_FILE_SIZE 对齐）

export interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  mime_type: string;
  url: string | null;
  isUploading: boolean;
  error: string | null;
  /** 后端双写返回的工作区相对路径，构造 FilePart 时透传 */
  workspace_path?: string;
}

export function useFileUpload() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const validateFile = (file: File, maxSizeMB?: number): string | null => {
    const maxSize = maxSizeMB ? maxSizeMB * 1024 * 1024 : DEFAULT_MAX_SIZE;
    const ext = getExt(file.name);
    if (!ALLOWED_EXTS.has(ext)) {
      return `不支持的文件类型: .${ext}`;
    }
    if (file.size > maxSize) {
      return `文件大小不能超过 ${maxSizeMB || 100}MB`;
    }
    return null;
  };

  const handleFileUpload = async (fileList: File[], maxSizeMB?: number) => {
    setUploadError(null);

    for (const file of fileList) {
      const error = validateFile(file, maxSizeMB);
      if (error) {
        setUploadError(error);
        return;
      }
    }

    const newFiles: UploadedFile[] = fileList.map((file) => ({
      id: `${Date.now()}-${Math.random()}`,
      file,
      name: file.name,
      size: file.size,
      mime_type: file.type,
      url: null,
      isUploading: true,
      error: null,
    }));

    setFiles((prev) => [...prev, ...newFiles]);

    for (const nf of newFiles) {
      try {
        const result: UploadFileResponse = await uploadFile(nf.file);
        setFiles((prev) =>
          prev.map((f) =>
            f.id === nf.id
              ? {
                  ...f,
                  url: result.url,
                  name: result.name,
                  workspace_path: result.workspace_path,
                  isUploading: false,
                }
              : f,
          ),
        );
      } catch (err) {
        logger.error('fileUpload', '文件上传失败', err);
        setFiles((prev) =>
          prev.map((f) =>
            f.id === nf.id ? { ...f, isUploading: false, error: '上传失败' } : f,
          ),
        );
      }
    }
  };

  const handleFileSelect = async (
    e: ChangeEvent<HTMLInputElement>,
    maxSizeMB?: number,
  ) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    await handleFileUpload(Array.from(fileList), maxSizeMB);
    e.target.value = '';
  };

  const handleFileDrop = async (fileList: FileList, maxSizeMB?: number) => {
    const filtered = Array.from(fileList).filter((f) => ALLOWED_EXTS.has(getExt(f.name)));
    if (filtered.length === 0) return;
    await handleFileUpload(filtered, maxSizeMB);
  };

  const handleRemoveFile = (fileId: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== fileId));
    setUploadError(null);
  };

  const handleRemoveAllFiles = () => {
    setFiles([]);
    setUploadError(null);
  };

  /** 提交时暂时移出文件；返回的函数可在明确拒绝时无损合并恢复。 */
  const detachFilesForSubmission = (): (() => void) => {
    const snapshot = files;
    setFiles([]);
    setUploadError(null);
    return () => {
      setFiles((current) => {
        const currentIds = new Set(current.map((file) => file.id));
        return [...snapshot.filter((file) => !currentIds.has(file.id)), ...current];
      });
    };
  };

  const clearUploadError = () => setUploadError(null);

  const isUploading = files.some((f) => f.isUploading);
  const hasFiles = files.length > 0;

  return {
    files,
    isUploading,
    uploadError,
    hasFiles,
    handleFileSelect,
    handleFileDrop,
    handleFileUpload,       // 暴露：供 InputArea handleUnifiedFiles 统一分流后直接调用
    handleRemoveFile,
    handleRemoveAllFiles,
    detachFilesForSubmission,
    clearUploadError,
  };
}
