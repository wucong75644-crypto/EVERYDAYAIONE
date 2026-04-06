/**
 * 文件相关工具函数（共享）
 *
 * FileCard 和 FilePreviewModal 共用。
 */

/** 文件类型图标映射 */
export function getFileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(ext)) return '\uD83D\uDCCA';
  if (ext === 'pdf') return '\uD83D\uDCC4';
  if (['doc', 'docx', 'txt', 'md'].includes(ext)) return '\uD83D\uDCC3';
  if (['zip', 'rar', '7z'].includes(ext)) return '\uD83D\uDCE6';
  return '\uD83D\uDCCE';
}

/** 格式化文件大小 */
export function formatFileSize(bytes?: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}
