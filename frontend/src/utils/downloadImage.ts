/**
 * 图片下载工具
 *
 * 工作区 OSS CDN 图片 → 走 CDN 直连(加 response-content-disposition query 参数
 *   让 CDN 返回 attachment header,浏览器必下载,不经过后端)
 * 外部图片 → fetch + blob 兼容路径
 */

/** 是否为我们的 OSS CDN 资源(workspace/images/videos 三种前缀) */
function isOurOssResource(url: string): boolean {
  return /^https?:\/\/[^/]+\/(workspace|images|videos)\//.test(url);
}

/** 给 OSS URL 拼 attachment 参数,让 CDN 响应强制下载 */
function buildOssDownloadUrl(url: string, filename: string): string {
  const encoded = encodeURIComponent(filename);
  const cd = `attachment; filename*=UTF-8''${encoded}`;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}response-content-disposition=${encodeURIComponent(cd)}`;
}

function triggerDownload(url: string, filename: string): void {
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

/** 从 OSS URL 推断扩展名(走 CDN 直连分支用) */
function getExtensionFromUrl(url: string): string {
  const pathname = url.split('?')[0].toLowerCase();
  const m = /\.(png|jpe?g|gif|webp|svg)(?:$|[?#])/i.exec(pathname);
  return m ? (m[1] === 'jpeg' ? 'jpg' : m[1]) : 'png';
}

/**
 * 图片下载
 *
 * @param imageUrl - 图片 URL(工作区图自动走 CDN 直连,外部图走 fetch)
 * @param filename - 下载文件名(不含扩展名 — 内部自动追加)
 * @param options - 可选配置
 * @param options.cors - cors 模式(默认 true,仅 fetch fallback 用)
 */
export async function downloadImage(
  imageUrl: string,
  filename: string,
  options: { cors?: boolean } = {},
): Promise<void> {
  // OSS 图片 → CDN 直连(query 参数让响应头变 attachment)
  if (isOurOssResource(imageUrl)) {
    const ext = getExtensionFromUrl(imageUrl);
    const fullName = `${filename}.${ext}`;
    triggerDownload(buildOssDownloadUrl(imageUrl, fullName), fullName);
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
  triggerDownload(blobUrl, `${filename}.${ext}`);
  URL.revokeObjectURL(blobUrl);
}
