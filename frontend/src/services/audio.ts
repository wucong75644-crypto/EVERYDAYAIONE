/**
 * 音频上传服务
 */

import { API_BASE_URL } from './api';

export interface AudioUploadResponse {
  audio_url: string;
  duration: number; // 音频时长（秒）
  size: number; // 文件大小（字节）
}

/**
 * 上传音频文件
 */
export async function uploadAudio(audioBlob: Blob): Promise<AudioUploadResponse> {
  const formData = new FormData();

  // 根据 MIME 类型确定文件扩展名
  const mimeType = audioBlob.type;
  const extension = mimeType.includes('webm') ? 'webm' : 'mp4';
  const filename = `audio_${Date.now()}.${extension}`;

  formData.append('file', audioBlob, filename);

  // 使用原生 fetch 上传，因为需要设置 multipart/form-data
  const token = localStorage.getItem('access_token');
  const orgId = localStorage.getItem('current_org_id');
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (orgId) headers['X-Org-Id'] = orgId;

  const response = await fetch(`${API_BASE_URL}/audio/upload`, {
    method: 'POST',
    headers,
    body: formData,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`上传失败: ${errorText || response.statusText}`);
  }

  return response.json();
}

