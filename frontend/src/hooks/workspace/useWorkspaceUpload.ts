import { useCallback, useState } from 'react';
import {
  uploadToWorkspace,
  type WorkspaceFileItem,
} from '../../services/workspace';
import { logger } from '../../utils/logger';
import type { FetchWorkspaceList, SetWorkspaceError } from './types';

export function useWorkspaceUpload(
  currentPath: string,
  fetchList: FetchWorkspaceList,
  isActivePath: (path: string) => boolean,
  setError: SetWorkspaceError,
) {
  const [uploadingFiles, setUploadingFiles] = useState<Map<string, WorkspaceFileItem>>(
    new Map(),
  );
  const upload = useCallback(async (files: File[]): Promise<boolean> => {
    const uploadPath = currentPath;
    setError(null);
    setUploadingFiles((previous) => addPlaceholders(previous, files, uploadPath));

    for (const file of files) {
      try {
        await uploadToWorkspace(file, uploadPath, (percent) => {
          setUploadingFiles((previous) => updateProgress(previous, file.name, percent));
        });
        setUploadingFiles((previous) => removePlaceholder(previous, file.name));
      } catch (err) {
        if (isActivePath(uploadPath)) {
          setError(err instanceof Error ? err.message : '上传失败');
        }
        logger.error('useWorkspace', `上传失败: ${file.name}`, err);
        setUploadingFiles((previous) => removePlaceholder(previous, file.name));
        if (isActivePath(uploadPath)) await fetchList(uploadPath);
        return false;
      }
    }
    if (isActivePath(uploadPath)) await fetchList(uploadPath);
    return true;
  }, [currentPath, fetchList, isActivePath, setError]);

  return { uploadingFiles, upload };
}

function addPlaceholders(
  previous: Map<string, WorkspaceFileItem>,
  files: File[],
  currentPath: string,
): Map<string, WorkspaceFileItem> {
  const next = new Map(previous);
  for (const file of files) {
    next.set(file.name, {
      name: file.name,
      is_dir: false,
      size: file.size,
      modified: String(Math.floor(Date.now() / 1000)),
      cdn_url: null,
      mime_type: file.type || null,
      uploadProgress: 0,
      _uploadPath: currentPath,
    });
  }
  return next;
}

function updateProgress(
  previous: Map<string, WorkspaceFileItem>,
  name: string,
  percent: number,
): Map<string, WorkspaceFileItem> {
  const next = new Map(previous);
  const item = next.get(name);
  if (item) next.set(name, { ...item, uploadProgress: percent });
  return next;
}

function removePlaceholder(
  previous: Map<string, WorkspaceFileItem>,
  name: string,
): Map<string, WorkspaceFileItem> {
  const next = new Map(previous);
  next.delete(name);
  return next;
}
