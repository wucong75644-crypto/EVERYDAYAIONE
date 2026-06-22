/**
 * 把项目里几种不同形态的"文件对象"统一成 PreviewItem 接口。
 *
 * 调用方涉及三种来源：
 *   1. WorkspaceView：WorkspaceFileItem（list_workspace 返回结构）
 *   2. FileCard：FilePart（消息 content[] 内的文件项）
 *   3. ImagePreview：UploadedImage（输入框待发送的本地图）
 */

import type { WorkspaceFileItem } from '../services/workspace';
import type { FilePart } from '../types/message';
import type { PreviewItem } from './types';

/** 工作区列表项 → PreviewItem */
export function fromWorkspaceItem(item: WorkspaceFileItem, workspacePath: string): PreviewItem {
  return {
    url: item.cdn_url || undefined,
    workspacePath,
    filename: item.name,
    mimeType: item.mime_type,
    size: item.size,
  };
}

/** 消息附件 FilePart → PreviewItem */
export function fromFilePart(file: FilePart): PreviewItem {
  return {
    url: file.url || undefined,
    workspacePath: file.workspace_path,
    filename: file.name,
    mimeType: file.mime_type,
    size: file.size,
  };
}

/** 输入框上传图（blob URL） → PreviewItem */
export function fromBlobImage(opts: { previewUrl: string; filename: string }): PreviewItem {
  return {
    url: opts.previewUrl,
    filename: opts.filename,
    // 函数名已保证输入是图片：注入 mimeType 兜底，让 ImageAdapter 在 filename 无扩展名时仍能命中
    mimeType: 'image/*',
  };
}
