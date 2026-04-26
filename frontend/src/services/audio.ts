/**
 * 音频上传服务
 *
 * 使用 axios api 实例上传（走拦截器，支持 401 无感刷新）。
 * axios 对 FormData 自动设置 multipart/form-data + boundary。
 */

import api from './api';

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

  const response = await api.post<AudioUploadResponse>('/audio/upload', formData);
  return response.data;
}

