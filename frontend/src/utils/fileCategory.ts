/**
 * 文件分类工具（工作区 Tab 筛选 + 预览路由分发用）
 *
 * 行业惯例：扩展名白名单为主，mime_type 兜底。
 * mime 来自后端 mimetypes.guess_type，本质也是扩展名查表，
 * 但前端白名单更可控、易测试，且对 mime 缺失场景更稳健。
 */

export const IMAGE_EXTS = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'heic',
]);

export const VIDEO_EXTS = new Set([
  'mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v',
]);

export type FileCategory = 'image' | 'video' | 'document';
export type CategoryFilter = 'all' | 'images' | 'documents';

interface CategorizableItem {
  name: string;
  mime_type?: string | null;
}

/** 判定单个文件的分类 */
export function categorize(item: CategorizableItem): FileCategory {
  const ext = item.name.split('.').pop()?.toLowerCase() ?? '';
  if (IMAGE_EXTS.has(ext) || item.mime_type?.startsWith('image/')) return 'image';
  if (VIDEO_EXTS.has(ext) || item.mime_type?.startsWith('video/')) return 'video';
  return 'document';
}

/** 判断文件是否属于当前 Tab 筛选 */
export function matchesFilter(item: CategorizableItem, filter: CategoryFilter): boolean {
  if (filter === 'all') return true;
  const cat = categorize(item);
  if (filter === 'images') return cat === 'image' || cat === 'video';
  if (filter === 'documents') return cat === 'document';
  return true;
}

/** 双击是否应该弹图片预览 */
export function canPreviewImage(item: CategorizableItem): boolean {
  return categorize(item) === 'image';
}

/** 双击是否应该弹视频预览 */
export function canPreviewVideo(item: CategorizableItem): boolean {
  return categorize(item) === 'video';
}
