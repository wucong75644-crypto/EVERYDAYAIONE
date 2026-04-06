/**
 * 通用文件下载（fetch + blob）
 *
 * 支持所有文件类型（xlsx/csv/pdf 等），直接用原始文件名。
 */

export async function downloadFile(
  url: string,
  filename: string,
  options: { cors?: boolean } = {},
): Promise<void> {
  const { cors = true } = options;
  const fetchOptions: RequestInit = cors
    ? { mode: 'cors', credentials: 'omit' }
    : {};

  const response = await fetch(url, fetchOptions);
  if (!response.ok) {
    throw new Error(`下载失败: ${response.status}`);
  }

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  URL.revokeObjectURL(blobUrl);
}
