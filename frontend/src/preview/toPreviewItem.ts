/**
 * 把项目里几种不同形态的"文件对象"统一成 PreviewItem 接口。
 *
 * 调用方涉及三种来源：
 *   1. WorkspaceView：WorkspaceFileItem（list_workspace 返回结构）
 *   2. FileCard：FilePart（消息 content[] 内的文件项）
 *   3. ImagePreview：UploadedImage（输入框待发送的本地图）
 */

import type { WorkspaceFileItem } from '../services/workspace';
import type { FilePart, ImageAsset } from '../types/message';
import { pickOriginalImageUrl, toDisplayThumbnailUrl, toOriginalImageUrl } from '../utils/imageUrlRules';
import type { PreviewItem } from './types';

/** 工作区列表项 → PreviewItem */
export function fromWorkspaceItem(item: WorkspaceFileItem, workspacePath: string): PreviewItem {
  const originalUrl = toOriginalImageUrl(item.cdn_url);
  return {
    url: originalUrl || undefined,
    thumbnailUrl: item.thumbnail_url ? toDisplayThumbnailUrl(item.thumbnail_url, originalUrl) : undefined,
    workspacePath,
    filename: item.name,
    mimeType: item.mime_type,
    size: item.size,
  };
}

/** 消息附件 FilePart → PreviewItem */
export function fromFilePart(file: FilePart): PreviewItem {
  const originalUrl = toOriginalImageUrl(file.url);
  return {
    url: originalUrl || undefined,
    workspacePath: file.workspace_path,
    filename: file.name,
    mimeType: file.mime_type,
    size: file.size,
  };
}

/** 输入框上传图（blob URL） → PreviewItem */
export function fromBlobImage(opts: {
  previewUrl: string;
  filename: string;
  originalUrl?: string | null;
  thumbnailUrl?: string | null;
}): PreviewItem {
  const originalUrl = pickOriginalImageUrl(opts.originalUrl, opts.previewUrl);
  return {
    url: originalUrl || undefined,
    thumbnailUrl: opts.thumbnailUrl
      ? toDisplayThumbnailUrl(opts.thumbnailUrl, originalUrl)
      : undefined,
    filename: opts.filename,
    // 函数名已保证输入是图片：注入 mimeType 兜底，让 ImageAdapter 在 filename 无扩展名时仍能命中
    mimeType: 'image/*',
  };
}

/** 消息图片资产 → PreviewItem（主体预览/下载用原图，缩略条用 thumbnailUrl） */
export function fromImageAsset(asset: ImageAsset, fallbackFilename: string): PreviewItem {
  const originalUrl = toOriginalImageUrl(asset.originalUrl);
  return {
    url: originalUrl || undefined,
    thumbnailUrl: asset.thumbnailUrl
      ? toDisplayThumbnailUrl(asset.thumbnailUrl, originalUrl)
      : undefined,
    filename: asset.filename || fallbackFilename,
    mimeType: 'image/*',
  };
}
