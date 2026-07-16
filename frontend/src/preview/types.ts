/**
 * 预览适配器（Preview Adapter）类型定义
 *
 * 设计目标：建立统一的预览适配器接口，让「文件类型 → 渲染方式」的决策
 * 收敛到一个 registry，加新类型只需写一个 adapter + 注册一行。
 *
 * 详见 docs/document/TECH_预览适配器架构.md
 */

import type { ComponentType } from 'react';

/**
 * 预览项 — 统一的输入数据结构，所有调用方（工作区/聊天/输入框）都构造此类型。
 */
export interface PreviewItem {
  /** CDN URL（首选）— 也可以是 blob URL（聊天输入框上传图） */
  url?: string;
  /** 图片缩略图 URL；仅用于列表/缩略条展示，不能用于下载或主体预览 */
  thumbnailUrl?: string;
  /** workspace 相对路径 — fallback 后端代理时需要 */
  workspacePath?: string;
  /** 文件名（含扩展名，用于决策 + 显示）*/
  filename: string;
  /** MIME 类型（如 list_workspace 返回的 mime_type）*/
  mimeType?: string | null;
  /** 文件大小（字节）*/
  size?: number;
}

/**
 * Adapter 渲染组件接收的统一 props。
 *
 * - `item`：当前要预览的项
 * - `siblings`：同分类的兄弟项列表（单文件预览时 = [item]）
 * - `index`：当前项在 siblings 中的索引
 * - `onNavigate`：切换索引（图片/视频用，文档类通常忽略）
 * - `onDelete`：可选删除回调（仅聊天附件预览场景使用）
 */
export interface PreviewCommonProps {
  item: PreviewItem;
  siblings: PreviewItem[];
  index: number;
  onClose: () => void;
  onNavigate: (newIndex: number) => void;
  onDelete?: () => void;
}

/**
 * 适配器定义。每种文件类型注册一个 adapter 到 registry。
 */
export interface PreviewAdapter {
  /** 唯一 id（用于日志/调试/React key） */
  id: string;
  /** 友好名（用于 fallback/错误提示）*/
  label: string;
  /** 命中规则：根据扩展名 / mimeType / 其他属性判断 */
  match: (item: PreviewItem) => boolean;
  /** 优先级：数字大优先；命中多个时取最高 */
  priority: number;
  /** 渲染组件 */
  Component: ComponentType<PreviewCommonProps>;
  /** 是否支持上下张（true → PreviewHost 知道兄弟列表有意义）*/
  supportsNavigation: boolean;
}

/**
 * 预览状态机
 */
export type PreviewState =
  | { kind: 'closed' }
  | { kind: 'open'; items: PreviewItem[]; index: number };

/** 提取文件扩展名（小写，不含点） */
export function extOf(filename: string): string {
  return filename.split('.').pop()?.toLowerCase() ?? '';
}
