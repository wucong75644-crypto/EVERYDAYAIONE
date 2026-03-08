/**
 * 图片下载工具函数
 *
 * 通过 fetch + blob 方式下载图片，避免浏览器直接打开图片。
 */

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
 * @param imageUrl - 图片 URL
 * @param filename - 下载文件名（不含扩展名）
 * @param options - 可选配置
 * @param options.cors - 是否使用 cors 模式（默认 true）
 */
export async function downloadImage(
  imageUrl: string,
  filename: string,
  options: { cors?: boolean } = {},
): Promise<void> {
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
