/**
 * Workspace 文件上传服务
 *
 * 上传文件到用户 workspace（ossfs 目录），供 AI 分析。
 */

import { request } from './api';

export interface WorkspaceUploadResponse {
  filename: string;
  path: string;
  size: number;
  cdn_url: string | null;
}

export interface WorkspaceFileItem {
  name: string;
  is_dir: boolean;
  size: number;
  modified: string;
}

export interface WorkspaceListResponse {
  path: string;
  items: WorkspaceFileItem[];
  total: number;
}

/** workspace 允许的文件扩展名 */
export const WORKSPACE_ALLOWED_EXTENSIONS = new Set([
  'txt', 'csv', 'json', 'yaml', 'yml', 'xml', 'md', 'log', 'tsv',
  'py', 'js', 'ts', 'html', 'css', 'sql',
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
  'zip',
]);

/** 最大文件大小 (50MB) */
export const WORKSPACE_MAX_FILE_SIZE = 50 * 1024 * 1024;

/**
 * 上传文件到 workspace
 */
export async function uploadToWorkspace(file: File): Promise<WorkspaceUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return request<WorkspaceUploadResponse>({
    method: 'POST',
    url: '/files/workspace/upload',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

/**
 * 列出 workspace 文件
 */
export async function listWorkspace(path = '.'): Promise<WorkspaceListResponse> {
  return request<WorkspaceListResponse>({
    method: 'GET',
    url: '/files/workspace/list',
    params: { path },
  });
}
