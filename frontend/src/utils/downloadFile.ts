/**
 * 通用文件下载（fetch + blob）
 *
 * 支持所有文件类型（xlsx/csv/pdf 等），直接用原始文件名。
 */

export async function downloadFile(
  url: string,
  filename: string,
): Promise<void> {
  try {
    // 优先用 fetch + blob（能指定文件名，不跳转）
    const response = await fetch(url, { mode: 'cors', credentials: 'omit' });
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
    // CORS 失败时用 <a target="_blank">（会开新标签但能下载）
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }
}
