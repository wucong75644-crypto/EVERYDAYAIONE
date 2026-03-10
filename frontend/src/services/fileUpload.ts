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
}

/**
 * 上传文档文件（FormData）
 *
 * 文件会上传到 OSS 并返回 CDN URL + 元信息。
 */
export async function uploadFile(file: File): Promise<UploadFileResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return request<UploadFileResponse>({
    method: 'POST',
    url: '/files/upload',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}
