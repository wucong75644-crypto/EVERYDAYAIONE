/**
 * 通用文件下载
 *
 * 工作区 OSS CDN 资源 → 直接走 CDN(加 OSS query 参数 response-content-disposition)
 *   CDN 看到这个参数,响应头自动带 Content-Disposition: attachment,
 *   浏览器必下载。不经过后端,CDN 边缘节点直接服务,最快。
 * 外部 URL → fetch + blob 兼容路径,失败 iframe fallback。
 */

/** 是否为工作区 OSS CDN URL */
function isWorkspaceUrl(url: string): boolean {
  return /^https?:\/\/[^/]+\/workspace\//.test(url);
}

/** 给 OSS URL 拼 attachment 参数,让 CDN 返回的响应强制下载 */
function buildOssDownloadUrl(url: string, filename: string): string {
  // RFC 5987 编码,支持中文文件名
  const encoded = encodeURIComponent(filename);
  const cd = `attachment; filename*=UTF-8''${encoded}`;
  const sep = url.includes('?') ? '&' : '?';
  // 整个 cd 值再做一次 URL 编码作为 query value(OSS 要求)
  return `${url}${sep}response-content-disposition=${encodeURIComponent(cd)}`;
}

/** 用 a.click 触发浏览器原生下载(配合 attachment header 必下载) */
function triggerDownload(url: string, filename: string): void {
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
  // 工作区资源 → CDN 直连(OSS query 参数控制响应头)
  if (isWorkspaceUrl(url)) {
    triggerDownload(buildOssDownloadUrl(url, filename), filename);
    return;
  }

  // 外部 URL fallback:fetch + blob → a.download
  try {
    const response = await fetch(url, { headers });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    triggerDownload(blobUrl, filename);
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
