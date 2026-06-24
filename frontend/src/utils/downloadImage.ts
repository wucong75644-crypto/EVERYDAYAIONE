/**
 * 图片下载工具
 *
 * 工作区图片(OSS CDN URL 含 `/workspace/`) → 走后端代理 /files/workspace/download_zip
 *   (统一入口,强制 attachment,跨域无忧)
 * 外部图片 → fetch + blob 兼容路径
 */

import { downloadWorkspaceZip } from '../services/workspace';

function isWorkspaceUrl(url: string): boolean {
  return /^https?:\/\/[^/]+\/workspace\//.test(url);
}

/** 从 Blob MIME 类型获取文件扩展名 */
function getExtensionFromBlob(blob: Blob): string {
  const mimeMap: Record<string, string> = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/svg+xml': 'svg',
  };
  return mimeMap[blob.type] || 'png';
}

/**
 * 通过 fetch + blob 下载图片
 *
 * @param imageUrl - 图片 URL(工作区图自动走代理,外部图走 fetch)
 * @param filename - 下载文件名(不含扩展名)— 仅外部图 fetch 路径使用
 * @param options - 可选配置
 * @param options.cors - 是否 cors 模式(默认 true,仅 fetch fallback 用)
 */
export async function downloadImage(
  imageUrl: string,
  filename: string,
  options: { cors?: boolean } = {},
): Promise<void> {
  // 工作区图片 → 后端代理(跟批量下载统一入口)
  if (isWorkspaceUrl(imageUrl)) {
    await downloadWorkspaceZip([imageUrl]);
    return;
  }

  const { cors = true } = options;
  const fetchOptions: RequestInit = cors
    ? { mode: 'cors', credentials: 'omit' }
    : {};

  const response = await fetch(imageUrl, fetchOptions);
  if (!response.ok) throw new Error('下载失败');

  const blob = await response.blob();
  const ext = getExtensionFromBlob(blob);
  const blobUrl = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = `${filename}.${ext}`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
}
