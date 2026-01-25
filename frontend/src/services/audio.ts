/**
 * 音频上传服务
 */

import { request, API_BASE_URL } from './api';

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
  const response = await fetch(`${API_BASE_URL}/audio/upload`, {
    method: 'POST',
    headers: {
      'Authorization': token ? `Bearer ${token}` : '',
    },
    body: formData,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`上传失败: ${errorText || response.statusText}`);
  }

  return response.json();
}

/**
 * 获取音频文件信息
 */
export async function getAudioInfo(audioUrl: string): Promise<{
  duration: number;
  size: number;
}> {
  return request<{ duration: number; size: number }>({
    method: 'GET',
    url: '/audio/info',
    params: { url: audioUrl },
  });
}

/**
 * 删除音频文件
 */
export async function deleteAudio(audioUrl: string): Promise<void> {
  return request<void>({
    method: 'DELETE',
    url: '/audio/delete',
    data: { audio_url: audioUrl },
  });
}
