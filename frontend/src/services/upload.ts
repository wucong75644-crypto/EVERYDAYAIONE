/**
 * 文件上传服务
 *
 * 提供图片上传等文件上传功能
 */

import { request } from './api';

export interface UploadImageResponse {
  url: string;
}

/**
 * 上传图片（FormData）
 *
 * 使用 FormData 直接上传文件，避免 base64 编码带来的 33% 体积膨胀。
 * 图片会上传到 OSS 并返回 CDN URL。
 */
export async function uploadImageFile(file: File): Promise<UploadImageResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return request<UploadImageResponse>({
    method: 'POST',
    url: '/images/upload',
    data: formData,
  });
}
