/**
 * 通用文件下载
 *
 * 我们的 OSS 资源(workspace/images/videos):
 *   - image/video/audio → ObjectMeta 自带 Content-Disposition: attachment,
 *     a.click 走 CDN 直连必下载(最快)
 *   - PDF → OSS 不加 attachment(避免破坏 iframe 预览),改走后端代理
 *     /files/workspace/preview?url=...&disposition=attachment
 *   - Excel/Word/ZIP 等 → 浏览器 MIME 默认就触发下载,无需特殊处理
 * 外部 URL → fetch + blob 兼容路径,失败时 iframe fallback。
 */

import { API_BASE_URL } from '../services/api';

/** 是否为我们的 OSS CDN 资源(workspace/images/videos 三种前缀) */
function isOurOssResource(url: string): boolean {
  return /^https?:\/\/[^/]+\/(workspace|images|videos)\//.test(url);
}

/** PDF 等浏览器默认内嵌渲染但 OSS Meta 未设 attachment 的类型 → 走代理 */
function needsProxyDownload(filename: string): boolean {
  return /\.pdf(\?|$)/i.test(filename);
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

export async function downloadFile(
  url: string,
  filename: string,
  headers?: Record<string, string>,
): Promise<void> {
  // 我们的 OSS 资源
  if (isOurOssResource(url)) {
    // PDF: 走后端代理(避免 iframe 预览受 attachment 影响)
    if (needsProxyDownload(filename)) {
      const proxyUrl = `${API_BASE_URL}/files/workspace/preview`
        + `?url=${encodeURIComponent(url)}&disposition=attachment`;
      triggerClick(proxyUrl, filename);
      return;
    }
    // image/video/audio/Excel/Word/ZIP 等 → CDN 直连 a.click 即下载
    triggerClick(url, filename);
    return;
  }

  // 外部 URL fallback:fetch + blob → a.download
  try {
    const response = await fetch(url, { headers });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    triggerClick(blobUrl, filename);
    URL.revokeObjectURL(blobUrl);
  } catch {
    // CORS 失败时用隐藏 iframe 触发浏览器原生下载
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = url;
    document.body.appendChild(iframe);
    setTimeout(() => {
      try { document.body.removeChild(iframe); } catch { /* already removed */ }
    }, 30000);
  }
}
