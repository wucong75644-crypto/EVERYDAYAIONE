import { useCallback, useState } from 'react';
import type { WorkspaceFile } from '../../../services/workspace';
import { categorize } from '../../../utils/fileCategory';

export function useWorkspaceAttachmentState() {
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFile[]>([]);

  const addWorkspaceFile = useCallback((file: WorkspaceFile) => {
    setWorkspaceFiles((current) => current.some(
      (item) => item.workspace_path === file.workspace_path,
    ) ? current : [...current, file]);
  }, []);

  const removeWorkspaceFile = useCallback((workspacePath: string) => {
    setWorkspaceFiles((current) => current.filter(
      (file) => file.workspace_path !== workspacePath,
    ));
  }, []);

  const consumeWorkspaceFiles = useCallback(() => setWorkspaceFiles([]), []);

  const clearWorkspaceImages = useCallback(() => {
    setWorkspaceFiles((current) => current.filter((file) => categorize(file) !== 'image'));
  }, []);

  const detachWorkspaceFiles = useCallback(() => {
    const snapshot = workspaceFiles;
    setWorkspaceFiles([]);
    return () => setWorkspaceFiles((current) => {
      const currentPaths = new Set(current.map((file) => file.workspace_path));
      return [
        ...snapshot.filter((file) => !currentPaths.has(file.workspace_path)),
        ...current,
      ];
    });
  }, [workspaceFiles]);

  return {
    workspaceFiles,
    addWorkspaceFile,
    removeWorkspaceFile,
    consumeWorkspaceFiles,
    clearWorkspaceImages,
    detachWorkspaceFiles,
  };
}
