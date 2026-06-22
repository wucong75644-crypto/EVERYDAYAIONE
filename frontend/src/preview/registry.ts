/**
 * 预览适配器注册表 — 单一真相源
 *
 * 加新文件类型只需：
 *   1. 在 adapters/ 下新建一个 adapter 文件
 *   2. 在这里 import 并 push 到 adapters[]
 *
 * 不要再去改 fileCategory.canPreview*、FilePreviewModal.PREVIEWABLE_EXTS
 * 等分散的白名单 —— 它们已经被废弃。
 */

import type { PreviewAdapter, PreviewItem } from './types';
import { IMAGE_EXTS, VIDEO_EXTS } from '../utils/fileCategory';
import { imageAdapter } from './adapters/ImageAdapter';
import { videoAdapter } from './adapters/VideoAdapter';
import { textAdapter } from './adapters/TextAdapter';
import { spreadsheetAdapter } from './adapters/SpreadsheetAdapter';
import { pdfAdapter } from './adapters/PdfAdapter';
import { docxAdapter } from './adapters/DocxAdapter';
import { pptxAdapter } from './adapters/PptxAdapter';
import { fallbackAdapter } from './adapters/FallbackAdapter';

// 静态注册表（按 priority 降序）。
// 加新 adapter：在此 import + push，无需调用任何注册函数。
//
// 优先级约定：
//   image/video        100  (binary 类型，命中即用)
//   pdf/spreadsheet/   80   (文档类，扩展名精确匹配)
//     docx/pptx
//   text               50   (兜底文本/代码)
//   fallback           0    (always-match，最低优先级，未支持类型显示提示)
const adapters: PreviewAdapter[] = [
  imageAdapter,
  videoAdapter,
  pdfAdapter,
  spreadsheetAdapter,
  docxAdapter,
  pptxAdapter,
  textAdapter,
  fallbackAdapter, // always-match，必须最后注册
].sort((a, b) => b.priority - a.priority);

/**
 * 解析给定预览项应使用哪个 adapter
 *
 * 命中多个时按 priority 取最高（image/video=100 优于 text=50）。
 * 任何项最终都会命中至少一个（fallback 是 always-match）。
 */
export function resolveAdapter(item: PreviewItem): PreviewAdapter | null {
  for (const a of adapters) {
    if (a.match(item)) return a;
  }
  return null;
}

/**
 * 该项是否能被预览（不是 fallback 即为可预览）
 *
 * 替代旧的 `canPreview(name: string)` API — 参数从 string 改为 PreviewItem，
 * 信息更完整（含 mimeType / workspacePath，未来扩展性好）。
 */
export function canPreview(item: PreviewItem): boolean {
  const a = resolveAdapter(item);
  return !!a && a.id !== 'fallback';
}

/**
 * 暴露 IMAGE_EXTS / VIDEO_EXTS 给 adapter 内部使用
 * （避免每个 adapter 自己写白名单）
 */
export { IMAGE_EXTS, VIDEO_EXTS };
