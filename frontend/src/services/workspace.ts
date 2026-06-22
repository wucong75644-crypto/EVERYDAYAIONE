/**
 * Workspace 文件管理 API 服务
 *
 * 整合原 workspaceUpload.ts 全部功能，新增 delete/mkdir/rename/move。
 */

import { request, API_BASE_URL } from './api';

// ============================================================
// Workspace 文件代理（绕过 CDN CORS）
// ============================================================

/** 构造后端代理预览 URL（绕过 CDN CORS） */
export function getWorkspacePreviewUrl(workspacePath: string): string {
  return `${API_BASE_URL}/files/workspace/preview?path=${encodeURIComponent(workspacePath)}`;
}

/** 构造认证 headers（用于后端代理的 fetch 调用） */
export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = localStorage.getItem('access_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const orgId = localStorage.getItem('current_org_id');
  if (orgId) headers['X-Org-Id'] = orgId;
  return headers;
}

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
  /** 上传目标路径（内部用，仅占位项） */
  _uploadPath?: string;
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

/** 搜索 workspace 文件（递归关键词匹配文件名） */
export function searchWorkspace(q: string, limit = 20): Promise<WorkspaceSearchResponse> {
  return request<WorkspaceSearchResponse>({
    method: 'GET',
    url: '/files/workspace/search',
    params: { q, limit },
  });
}

export interface WorkspaceSearchResponse {
  items: (WorkspaceFileItem & { workspace_path?: string })[];
  total: number;
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

/**
 * 批量下载 workspace 文件为 ZIP。
 *
 * 后端流式打包返回 application/zip，前端用 fetch + blob 触发浏览器原生下载。
 * 超过 500 文件或 2GB 时后端返回 413。
 */
export async function downloadWorkspaceZip(paths: string[]): Promise<void> {
  if (paths.length === 0) throw new Error('未选择任何文件');

  const response = await fetch(`${API_BASE_URL}/files/workspace/download_zip`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ paths }),
  });

  if (!response.ok) {
    // 尝试解析后端 AppException 标准格式 { code, message }
    let message = '下载失败';
    try {
      const data = await response.json();
      message = data?.detail?.message || data?.message || message;
    } catch { /* ignore */ }
    throw new Error(message);
  }

  // 从 Content-Disposition 解析文件名（优先 RFC 5987 filename*=UTF-8''xxx）
  const disposition = response.headers.get('Content-Disposition') || '';
  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const asciiMatch = /filename="([^"]+)"/.exec(disposition);
  const filename = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : asciiMatch?.[1] || 'download.zip';

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
}
