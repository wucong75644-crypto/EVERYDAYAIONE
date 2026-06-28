/**
 * 相对时间格式化（中文，与后端 utils/time_context.format_relative_time 对齐）
 *
 * < 1 分钟       → "刚刚"
 * < 1 小时       → "约 N 分钟前"
 * < 24 小时      → "约 N 小时前"
 * ≥ 24 小时      → "约 N 天前"
 *
 * 无效输入返回 "未知时间前"
 */
export function formatRelativeCN(iso: string | null | undefined): string {
  if (!iso) return '未知时间前';
  try {
    const dt = new Date(iso);
    if (isNaN(dt.getTime())) return '未知时间前';
    const delta = (Date.now() - dt.getTime()) / 1000;
    if (delta < 60) return '刚刚';
    if (delta < 3600) return `约 ${Math.floor(delta / 60)} 分钟前`;
    if (delta < 86400) return `约 ${Math.floor(delta / 3600)} 小时前`;
    return `约 ${Math.floor(delta / 86400)} 天前`;
  } catch {
    return '未知时间前';
  }
}
