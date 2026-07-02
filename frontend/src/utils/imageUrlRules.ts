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

export function isThumbnailImageUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  return url.includes('/workspace-thumbnails/');
}

/** 原图规则：预览、下载、传模型必须使用，不允许携带 OSS 缩略参数。 */
export function toOriginalImageUrl(url: string | null | undefined): string {
  if (!url) return '';
  const normalized = removeOssProcessParam(url);
  return isThumbnailImageUrl(normalized) ? '' : normalized;
}

export function pickOriginalImageUrl(
  ...urls: Array<string | null | undefined>
): string {
  for (const url of urls) {
    const originalUrl = toOriginalImageUrl(url);
    if (originalUrl) return originalUrl;
  }
  return '';
}

/** 缩略图展示规则：只用于小图展示、缩略条、列表网格。 */
export function toDisplayThumbnailUrl(
  thumbnailUrl: string | null | undefined,
  fallbackOriginalUrl?: string | null,
): string {
  if (thumbnailUrl) return removeOssProcessParam(thumbnailUrl);
  return toOriginalImageUrl(fallbackOriginalUrl);
}

/** 缩略图规则：只用于小图展示、缩略条、列表网格。 */
export function toThumbnailImageUrl(
  url: string | null | undefined,
  _width: number = 240,
  _mode: 'lfit' | 'fill' = 'lfit',
): string {
  return toDisplayThumbnailUrl(url);
}
