/**
 * 文件上传服务
 *
 * 提供 PDF 等文档文件上传功能
 */

import { request } from './api';

export interface UploadFileResponse {
  url: string;
  name: string;
  mime_type: string;
  size: number;
  /** 工作区相对路径（如 上传/2026-06/xxx.pdf），后端注册 file_path_cache 用 */
  workspace_path?: string;
}

/**
 * 上传文档文件（FormData）
 *
 * 双写到工作区 + OSS：
 * - 工作区落盘 上传/{YYYY-MM}/xxx.ext，供 file_analyze/code_execute 等工具读取
 * - OSS CDN URL 供前端展示 + 多模态消息引用
 * 返回字段含 workspace_path，前端构造 FilePart 时透传。
 */
export async function uploadFile(file: File): Promise<UploadFileResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return request<UploadFileResponse>({
    method: 'POST',
    url: '/files/upload',
    data: formData,
  });
}
