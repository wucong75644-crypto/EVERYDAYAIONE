/**
 * 图片下载
 *
 * 我们的 OSS 图片(workspace/images/videos) → ObjectMeta 自带
 *   Content-Disposition: attachment,a.click 必下载,走 CDN 边缘节点最快。
 * 外部图片 → fetch + blob 兼容路径(无 CD,无法直接 click 下载)。
 */

function isOurOssResource(url: string): boolean {
  return /^https?:\/\/[^/]+\/(workspace|images|videos)\//.test(url);
}

function triggerClick(url: string, filename: string): void {
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
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

/** 从 URL 路径推断扩展名(OSS 直连分支用) */
function getExtensionFromUrl(url: string): string {
  const pathname = url.split('?')[0].toLowerCase();
  const m = /\.(png|jpe?g|gif|webp|svg)(?:$|[?#])/i.exec(pathname);
  return m ? (m[1] === 'jpeg' ? 'jpg' : m[1]) : 'png';
}

/**
 * 图片下载
 *
 * @param imageUrl - 图片 URL(我们的 OSS 图自动走 CDN 直连,外部图走 fetch)
 * @param filename - 下载文件名(不含扩展名 — 内部自动追加)
 * @param options - 可选配置
 * @param options.cors - cors 模式(默认 true,仅 fetch fallback 用)
 */
export async function downloadImage(
  imageUrl: string,
  filename: string,
  options: { cors?: boolean } = {},
): Promise<void> {
  // 我们的 OSS 图片:ObjectMeta 自带 attachment → a.click 必下载
  if (isOurOssResource(imageUrl)) {
    const ext = getExtensionFromUrl(imageUrl);
    triggerClick(imageUrl, `${filename}.${ext}`);
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
  triggerClick(blobUrl, `${filename}.${ext}`);
  URL.revokeObjectURL(blobUrl);
}
