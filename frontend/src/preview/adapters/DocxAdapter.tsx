/**
 * DocxAdapter — Word .docx 预览
 *
 * 用 mammoth.js 在前端把 docx 解析为 HTML，再用 DOMPurify 清洗后渲染。
 * 流量仍走 CDN（fetchPreviewResponse 拉 buffer），跟 xlsx/pdf 同策略。
 *
 * mammoth 不支持的特性：
 *   - .doc 老二进制格式（要走 PptxAdapter 后端 LibreOffice 转换路径）
 *   - 复杂样式（批注、嵌入图表等可能丢失）
 * 这些场景在 onError 兜底显示，仍可下载查看完整版。
 */

import { useEffect, useState } from 'react';
import PreviewFrame from '../PreviewFrame';
import { fetchPreviewResponse } from '../fetchPreview';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';

function DocxAdapterComponent({ item, onClose }: PreviewCommonProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [html, setHtml] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const { response } = await fetchPreviewResponse(item);
        const arrayBuffer = await response.arrayBuffer();
        if (cancelled) return;

        // 动态 import 重库（~700KB），不进首屏 bundle
        const [{ default: mammoth }, { default: DOMPurify }] = await Promise.all([
          import('mammoth'),
          import('dompurify'),
        ]);
        const { value: rawHtml } = await mammoth.convertToHtml({ arrayBuffer });
        if (cancelled) return;

        // 防御 XSS：清洗 mammoth 输出的 HTML
        const safe = DOMPurify.sanitize(rawHtml);
        setHtml(safe);
      } catch (e) {
        if (cancelled) return;
        setError(`docx 解析失败：${(e as Error).message}。建议下载查看完整版`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [item]);

  return (
    <PreviewFrame item={item} onClose={onClose} loading={loading} error={error}>
      <div className="p-6">
        <article
          className="docx-preview mx-auto max-w-3xl bg-white text-gray-900 rounded-lg p-8 shadow-lg leading-relaxed"
          // mammoth + DOMPurify 已清洗，安全注入
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </PreviewFrame>
  );
}

function matchDocx(item: PreviewItem): boolean {
  return extOf(item.filename) === 'docx';
}

export const docxAdapter: PreviewAdapter = {
  id: 'docx',
  label: 'Word 文档',
  priority: 80,
  match: matchDocx,
  Component: DocxAdapterComponent,
  supportsNavigation: false,
};
