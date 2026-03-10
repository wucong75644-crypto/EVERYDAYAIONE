/**
 * 文件上传 Hook
 *
 * 处理 PDF 文件选择、校验、上传、预览和删除
 */

import { useState, type ChangeEvent } from 'react';
import { uploadFile, type UploadFileResponse } from '../services/fileUpload';
import { logger } from '../utils/logger';

const ALLOWED_TYPES = ['application/pdf'];
const DEFAULT_MAX_SIZE = 50 * 1024 * 1024; // 50MB

export interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  mime_type: string;
  url: string | null;
  isUploading: boolean;
  error: string | null;
}

export function useFileUpload() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const validateFile = (file: File, maxSizeMB?: number): string | null => {
    const maxSize = maxSizeMB ? maxSizeMB * 1024 * 1024 : DEFAULT_MAX_SIZE;
    if (!ALLOWED_TYPES.includes(file.type)) {
      return '仅支持 PDF 格式的文档';
    }
    if (file.size > maxSize) {
      return `文件大小不能超过 ${maxSizeMB || 50}MB`;
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
              ? { ...f, url: result.url, name: result.name, isUploading: false }
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
    const pdfFiles = Array.from(fileList).filter((f) => ALLOWED_TYPES.includes(f.type));
    if (pdfFiles.length === 0) return;
    await handleFileUpload(pdfFiles, maxSizeMB);
  };

  const handleRemoveFile = (fileId: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== fileId));
    setUploadError(null);
  };

  const handleRemoveAllFiles = () => {
    setFiles([]);
    setUploadError(null);
  };

  const clearUploadError = () => setUploadError(null);

  const isUploading = files.some((f) => f.isUploading);
  const uploadedFileUrls = files
    .filter((f) => f.url !== null)
    .map((f) => ({
      url: f.url as string,
      name: f.name,
      mime_type: f.mime_type,
      size: f.size,
    }));
  const hasFiles = files.length > 0;

  return {
    files,
    uploadedFileUrls,
    isUploading,
    uploadError,
    hasFiles,
    handleFileSelect,
    handleFileDrop,
    handleRemoveFile,
    handleRemoveAllFiles,
    clearUploadError,
  };
}
