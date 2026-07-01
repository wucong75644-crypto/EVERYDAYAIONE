const OSS_HOSTS_PATTERN = /cdn\.everydayai\.com\.cn|\.aliyuncs\.com/;

function removeOssProcessParam(url: string): string {
  const [withoutHash, hash = ''] = url.split('#');
  const queryStart = withoutHash.indexOf('?');
  if (queryStart < 0) return url;

  const base = withoutHash.slice(0, queryStart);
  const query = withoutHash.slice(queryStart + 1);
  const params = query
    .split('&')
    .filter((param) => param && param.split('=')[0] !== 'x-oss-process');
  const nextQuery = params.length > 0 ? `?${params.join('&')}` : '';
  const nextHash = hash ? `#${hash}` : '';
  return `${base}${nextQuery}${nextHash}`;
}

/** 原图规则：预览、下载、传模型必须使用，不允许携带 OSS 缩略参数。 */
export function toOriginalImageUrl(url: string | null | undefined): string {
  if (!url) return '';
  return removeOssProcessParam(url);
}

/** 缩略图规则：只用于小图展示、缩略条、列表网格。 */
export function toThumbnailImageUrl(
  url: string | null | undefined,
  width: number = 240,
  mode: 'lfit' | 'fill' = 'lfit',
): string {
  const originalUrl = toOriginalImageUrl(url);
  if (!originalUrl) return '';
  if (!OSS_HOSTS_PATTERN.test(originalUrl)) return originalUrl;
  const sep = originalUrl.includes('?') ? '&' : '?';
  return `${originalUrl}${sep}x-oss-process=image/resize,w_${width},m_${mode}`;
}
