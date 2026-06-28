/**
 * OSS 图片缩略图变换
 *
 * 阿里云 OSS 通过 URL 参数 `x-oss-process=image/resize,w_N` 生成缩略图，
 * CDN 边缘节点会缓存变换结果。同图取大缩略图 28× 体积差（实测 2.1MB → 74KB）。
 *
 * 行业标准做法：列表/网格用缩略图，Lightbox/详情用原图。
 *
 * 仅对项目 CDN（cdn.everydayai.com.cn）和 aliyuncs OSS 直链应用，
 * 外部 URL（如其他用户上传的链接）原样返回。
 */

const OSS_HOSTS_PATTERN = /cdn\.everydayai\.com\.cn|\.aliyuncs\.com/;

/**
 * 生成 OSS 缩略图 URL
 *
 * @param url 原始图片 URL
 * @param width 目标宽度（像素）；OSS lfit 模式 = 等比缩放不放大
 * @returns 含 x-oss-process 参数的缩略图 URL；非 OSS URL 原样返回
 */
export function ossThumbUrl(
  url: string | undefined | null,
  width: number = 240,
): string {
  if (!url) return '';
  if (!OSS_HOSTS_PATTERN.test(url)) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}x-oss-process=image/resize,w_${width},m_lfit`;
}
