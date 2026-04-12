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

/** 文件类型 → 背景色 CSS class（图标模式/文件卡片用） */
export function getFileIconColor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'text-red-500 dark:text-red-400';
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(ext)) return 'text-green-500 dark:text-green-400';
  if (['doc', 'docx'].includes(ext)) return 'text-blue-500 dark:text-blue-400';
  if (['ppt', 'pptx'].includes(ext)) return 'text-orange-500 dark:text-orange-400';
  if (['py', 'js', 'ts', 'html', 'css', 'sql'].includes(ext)) return 'text-purple-500 dark:text-purple-400';
  if (['zip', 'rar', '7z'].includes(ext)) return 'text-yellow-600 dark:text-yellow-400';
  return 'text-[var(--s-text-secondary)]';
}

/** 格式化文件大小 */
export function formatFileSize(bytes?: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}
