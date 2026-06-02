/**
 * 文件上传服务
 *
 * 提供图片上传等文件上传功能
 */

import { request } from './api';

export interface UploadImageResponse {
  url: string;
  /** 工作区文件名（带 UUID 后缀），LLM 引用与 file_path_cache 查询用 */
  name?: string;
  /** 工作区相对路径（如 上传/2026-06/xxx.png），后端注册 file_path_cache 用 */
  workspace_path?: string;
  size?: number;
  mime_type?: string;
}

/**
 * 上传图片（FormData）
 *
 * 双写到工作区 + OSS：
 * - 工作区落盘 上传/{YYYY-MM}/xxx.png，供 file_search/file_analyze 等工具读取
 * - OSS CDN URL 供视觉模型多模态注入 + 前端展示
 * 返回字段除 url 外还含 name + workspace_path，前端构造 ImagePart 时透传。
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
