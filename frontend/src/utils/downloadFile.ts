/**
 * 通用文件下载
 *
 * 工作区资源(OSS CDN URL 含 `/workspace/`) → 走后端代理 /files/workspace/download_zip
 *   (跟批量下载共用端点,后端强制 Content-Disposition: attachment,跨域无忧)
 * 外部 URL → fetch + blob 兼容路径,失败 iframe fallback
 */

import { downloadWorkspaceZip } from '../services/workspace';

/** 是否为工作区 OSS CDN URL(同步到 OSS workspace/ 路径下的资源) */
function isWorkspaceUrl(url: string): boolean {
  return /^https?:\/\/[^/]+\/workspace\//.test(url);
}

export async function downloadFile(
  url: string,
  filename: string,
  headers?: Record<string, string>,
): Promise<void> {
  // 工作区资源 → 后端代理(统一入口)
  if (isWorkspaceUrl(url)) {
    await downloadWorkspaceZip([url]);
    return;
  }

  // 外部 URL fallback:fetch + blob → a.download
  try {
    const response = await fetch(url, { headers });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(blobUrl);
  } catch {
    // CORS 失败时用隐藏 iframe 触发浏览器原生下载
    // （跨域 <a download> 会被浏览器忽略变成跳转，iframe 对 octet-stream 会触发下载）
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = url;
    document.body.appendChild(iframe);
    setTimeout(() => {
      try { document.body.removeChild(iframe); } catch { /* already removed */ }
    }, 30000);
  }
}
