/**
 * 预览资源 fetch 工具 — 共享给所有「需要拿文件 buffer 自渲染」的 adapter
 * （Pdf / Spreadsheet / Text / Docx）。Image/Video 用 <img>/<video> 不走这里。
 *
 * 沿用历史设计：CDN 优先 → 失败 fallback 到后端代理。
 * 新增：fallback 触发时 console.warn 让运维感知 CDN 故障频率，不再悄悄走兜底。
 */

import { getAuthHeaders, getWorkspacePreviewUrl } from '../services/workspace';
import type { PreviewItem } from './types';

export interface FetchPreviewResult {
  response: Response;
  /** 实际使用的 URL（用于错误日志/调试）*/
  fetchedFrom: 'cdn' | 'backend';
}

/**
 * 拉取预览资源 buffer。
 *
 * 优先 CDN，失败时 fallback 到后端代理（CDN 流量 99% 走前者；CDN 故障时
 * 后端兜底，并在控制台 warn 出告警）。
 *
 * @throws 当 CDN + 后端代理都失败时抛出明确错误
 */
export async function fetchPreviewResponse(item: PreviewItem): Promise<FetchPreviewResult> {
  // 第一选择：CDN URL
  if (item.url) {
    try {
      const r = await fetch(item.url);
      if (r.ok) return { response: r, fetchedFrom: 'cdn' };
      // 4xx/5xx 视为 CDN 故障
      // eslint-disable-next-line no-console
      console.warn('[preview] CDN responded non-ok, falling back to backend proxy', {
        url: item.url,
        status: r.status,
      });
    } catch (e) {
      // CORS / 网络错误
      // eslint-disable-next-line no-console
      console.warn('[preview] CDN fetch threw, falling back to backend proxy', {
        url: item.url,
        error: (e as Error).message,
      });
    }
  }

  // Fallback：后端代理（带认证 + org header）
  if (!item.workspacePath) {
    throw new Error('文件加载失败：缺少 workspace 路径，无法兜底');
  }
  const proxyUrl = getWorkspacePreviewUrl(item.workspacePath);
  const r = await fetch(proxyUrl, { headers: getAuthHeaders() });
  if (!r.ok) {
    throw new Error(`文件加载失败：HTTP ${r.status}`);
  }
  return { response: r, fetchedFrom: 'backend' };
}

/**
 * 构造预览资源 URL（不实际 fetch，用于 iframe src / img src 直链场景）
 *
 * Phase 1 之后留给 PdfAdapter 等使用：如果 item.url 存在用 CDN，否则用后端代理。
 */
export function resolvePreviewUrl(item: PreviewItem): string | null {
  if (item.url) return item.url;
  if (item.workspacePath) return getWorkspacePreviewUrl(item.workspacePath);
  return null;
}
