/**
 * 图片 URL 工具函数
 *
 * 统一处理图片 URL 解析逻辑，支持逗号分隔的多图格式
 */

/**
 * 解析图片 URL 字符串为数组
 *
 * 支持逗号分隔的多图格式，自动去除空白和空值
 *
 * @param imageUrl - 图片 URL 字符串（可为 null/undefined）
 * @returns 图片 URL 数组
 *
 * @example
 * parseImageUrls('url1, url2, url3') // ['url1', 'url2', 'url3']
 * parseImageUrls('url1') // ['url1']
 * parseImageUrls(null) // []
 * parseImageUrls('') // []
 */
export function parseImageUrls(imageUrl: string | null | undefined): string[] {
  if (!imageUrl) return [];
  return imageUrl.split(',').map((url) => url.trim()).filter(Boolean);
}

/**
 * 获取第一张图片 URL
 *
 * 从逗号分隔的图片 URL 字符串中提取第一张
 *
 * @param imageUrl - 图片 URL 字符串（可为 null/undefined）
 * @returns 第一张图片 URL，无图片时返回 null
 *
 * @example
 * getFirstImageUrl('url1, url2') // 'url1'
 * getFirstImageUrl('url1') // 'url1'
 * getFirstImageUrl(null) // null
 */
export function getFirstImageUrl(imageUrl: string | null | undefined): string | null {
  if (!imageUrl) return null;
  const firstUrl = imageUrl.split(',')[0]?.trim();
  return firstUrl || null;
}
