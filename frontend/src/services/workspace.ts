/**
 * Workspace 文件管理 API 服务
 *
 * 整合原 workspaceUpload.ts 全部功能，新增 delete/mkdir/rename/move。
 */

import { request } from './api';

// ============================================================
// 类型定义
// ============================================================

export interface WorkspaceFileItem {
  name: string;
  is_dir: boolean;
  size: number;
  modified: string;
  cdn_url: string | null;
  mime_type: string | null;
  /** 上传进度 0~100，undefined 表示非上传状态 */
  uploadProgress?: number;
}

export interface WorkspaceListResponse {
  path: string;
  items: WorkspaceFileItem[];
  total: number;
}

export interface WorkspaceUploadResponse {
  filename: string;
  path: string;
  size: number;
  cdn_url: string | null;
}

export interface WorkspaceMoveResponse {
  success: boolean;
  new_path: string;
}

/** 供 Chat.tsx pendingWorkspaceFiles 使用 */
export interface WorkspaceFile {
  name: string;
  workspace_path: string;
  cdn_url: string | null;
  mime_type: string | null;
  size: number;
}

// ============================================================
// workspace 允许的文件扩展名（与后端保持一致）
// ============================================================

export const WORKSPACE_ALLOWED_EXTENSIONS = new Set([
  'txt', 'csv', 'json', 'yaml', 'yml', 'xml', 'md', 'log', 'tsv',
  'py', 'js', 'ts', 'html', 'css', 'sql',
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'zip',
]);

export const WORKSPACE_MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB

// ============================================================
// API 调用
// ============================================================

/** 列出 workspace 文件 */
export function listWorkspace(path = '.'): Promise<WorkspaceListResponse> {
  return request<WorkspaceListResponse>({
    method: 'GET',
    url: '/files/workspace/list',
    params: { path },
  });
}

/** 上传文件到 workspace */
export function uploadToWorkspace(
  file: File,
  targetDir = '.',
  onProgress?: (percent: number) => void,
): Promise<WorkspaceUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('target_dir', targetDir);
  return request<WorkspaceUploadResponse>({
    method: 'POST',
    url: '/files/workspace/upload',
    data: formData,
    timeout: 300000, // 大文件上传 5 分钟超时
    onUploadProgress: onProgress
      ? (e) => {
          if (e.total) {
            onProgress(Math.round((e.loaded * 100) / e.total));
          }
        }
      : undefined,
  });
}

/** 删除文件或空目录 */
export function deleteWorkspaceItem(path: string): Promise<{ success: boolean }> {
  return request({
    method: 'POST',
    url: '/files/workspace/delete',
    data: { path },
  });
}

/** 新建文件夹 */
export function mkdirWorkspace(path: string): Promise<{ success: boolean; path: string }> {
  return request({
    method: 'POST',
    url: '/files/workspace/mkdir',
    data: { path },
  });
}

/** 重命名文件或目录 */
export function renameWorkspaceItem(
  oldPath: string,
  newPath: string,
): Promise<{ success: boolean }> {
  return request({
    method: 'POST',
    url: '/files/workspace/rename',
    data: { old_path: oldPath, new_path: newPath },
  });
}

/** 移动文件到目标目录 */
export function moveWorkspaceItem(
  srcPath: string,
  destDir: string,
): Promise<WorkspaceMoveResponse> {
  return request({
    method: 'POST',
    url: '/files/workspace/move',
    data: { src_path: srcPath, dest_dir: destDir },
  });
}
