/**
 * 通用文件下载（fetch + blob）
 *
 * 支持所有文件类型（xlsx/csv/pdf 等），直接用原始文件名。
 */

export async function downloadFile(
  url: string,
  filename: string,
): Promise<void> {
  // 直接用 <a> 标签下载，不走 fetch（避免 CORS 问题）
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
