import { useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import type { UseWorkspaceReturn } from '../../hooks/useWorkspace';
import type { useFileSelection } from '../../hooks/useFileSelection';
import type { usePreview } from '../../preview/usePreview';
import { fromWorkspaceItem } from '../../preview/toPreviewItem';
import { resolveAdapter } from '../../preview/registry';
import type { PreviewItem } from '../../preview/types';
import type { WorkspaceFileItem } from '../../services/workspace';
import { downloadWorkspaceZip } from '../../services/workspace';
import { downloadFile } from '../../utils/downloadFile';
import { categorize, matchesFilter } from '../../utils/fileCategory';
import { toOriginalImageUrl } from '../../utils/imageUrlRules';
import { getFullPath } from './WorkspaceFileItem';

type Selection = ReturnType<typeof useFileSelection>;
type Preview = ReturnType<typeof usePreview>;

interface ItemActionOptions {
  workspace: UseWorkspaceReturn;
  selection: Selection;
  preview: Preview;
  addWorkspaceFile: (file: {
    name: string;
    workspace_path: string;
    cdn_url: string | null;
    mime_type: string | null;
    size: number;
  }) => void;
  navigateTo: (path: string) => void;
}

export function useWorkspaceItemActions(options: ItemActionOptions) {
  const { workspace, selection, preview, addWorkspaceFile, navigateTo } = options;
  const { currentPath, items, categoryFilter, multiSelectMode, upload } = workspace;
  const { selectedCount, selectedPaths } = selection;

  const filteredItems = useMemo(
    () => items.filter((item) => (
      item.is_dir ? categoryFilter === 'all' : matchesFilter(item, categoryFilter)
    )),
    [items, categoryFilter],
  );
  const imageItems = useMemo(
    () => filteredItems.filter(isImage),
    [filteredItems],
  );
  const videoItems = useMemo(
    () => filteredItems.filter(isVideo),
    [filteredItems],
  );
  const orderedPaths = useMemo(
    () => filteredItems.map((item) => getFullPath(currentPath, item.name)),
    [filteredItems, currentPath],
  );

  const handleSelect = useCallback((path: string, event: React.MouseEvent) => {
    if (multiSelectMode) selection.toggle(path);
    else selection.handleClick(path, orderedPaths, event);
  }, [multiSelectMode, orderedPaths, selection]);

  const handleOpen = useCallback((item: WorkspaceFileItem) => {
    if (item.is_dir) {
      navigateTo(getFullPath(currentPath, item.name));
      return;
    }
    const context = fromWorkspaceItem(item, getFullPath(currentPath, item.name));
    const adapterId = resolveAdapter(context)?.id;
    const source = adapterId === 'image'
      ? imageItems
      : adapterId === 'video' ? videoItems : null;
    const siblings: PreviewItem[] = source
      ? source.map((entry) => fromWorkspaceItem(
        entry,
        getFullPath(currentPath, entry.name),
      ))
      : [context];
    const found = source?.findIndex((entry) => entry.name === item.name) ?? 0;
    preview.open(siblings, found >= 0 ? found : 0);
  }, [currentPath, imageItems, navigateTo, preview, videoItems]);

  const handleSendToChat = useCallback((item: WorkspaceFileItem) => {
    const fullPath = getFullPath(currentPath, item.name);
    const selectedItems = selectedCount > 1 && selectedPaths.has(fullPath)
      ? items.filter((entry) => (
        !entry.is_dir && selectedPaths.has(getFullPath(currentPath, entry.name))
      ))
      : [item];
    for (const entry of selectedItems) {
      addWorkspaceFile(toAttachment(entry, currentPath));
    }
  }, [addWorkspaceFile, currentPath, items, selectedCount, selectedPaths]);

  const handleUpload = useCallback(async (files: File[]) => {
    if (await upload(files)) toast.success(`已上传 ${files.length} 个文件`);
  }, [upload]);

  const handleBatchDownload = useCallback(
    (item: WorkspaceFileItem) => downloadItem(item, currentPath, selectedPaths),
    [currentPath, selectedPaths],
  );
  const handleBatchDownloadAll = useCallback(
    () => downloadPaths(Array.from(selectedPaths)),
    [selectedPaths],
  );

  return {
    filteredItems, orderedPaths, handleSelect, handleOpen, handleSendToChat,
    handleUpload, handleBatchDownload, handleBatchDownloadAll,
  };
}

function isImage(item: WorkspaceFileItem): boolean {
  return !item.is_dir && categorize(item) === 'image' && Boolean(item.cdn_url);
}

function isVideo(item: WorkspaceFileItem): boolean {
  return !item.is_dir && categorize(item) === 'video' && Boolean(item.cdn_url);
}

function toAttachment(item: WorkspaceFileItem, currentPath: string) {
  return {
    name: item.name,
    workspace_path: getFullPath(currentPath, item.name),
    cdn_url: item.cdn_url ? toOriginalImageUrl(item.cdn_url) : null,
    mime_type: item.mime_type,
    size: item.size,
  };
}

async function downloadItem(
  item: WorkspaceFileItem,
  currentPath: string,
  selectedPaths: Set<string>,
): Promise<void> {
  const fullPath = getFullPath(currentPath, item.name);
  if (selectedPaths.size > 1 && selectedPaths.has(fullPath)) {
    await downloadPaths(Array.from(selectedPaths));
  } else if (item.is_dir) {
    await downloadPaths([fullPath], item.name);
  } else if (item.cdn_url) {
    downloadFile(toOriginalImageUrl(item.cdn_url), item.name);
  }
}

async function downloadPaths(paths: string[], label?: string): Promise<void> {
  if (paths.length === 0) return;
  const toastId = toast.loading(label ? `正在打包 ${label}...` : `正在打包 ${paths.length} 项...`);
  try {
    await downloadWorkspaceZip(paths);
    toast.success(label ? '已下载' : `已下载 ${paths.length} 项`, { id: toastId });
  } catch (error) {
    toast.error(error instanceof Error ? error.message : '下载失败', { id: toastId });
  }
}
