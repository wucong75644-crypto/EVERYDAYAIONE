/**
 * PptxAdapter — pptx / ppt / doc 通过后端 LibreOffice 转 PDF 预览
 *
 * 流程：
 *   1. POST /files/workspace/preview/render → 返回转换后的 PDF stream
 *   2. 把 stream 转 Blob URL
 *   3. 复用 PdfAdapter 同样的 react-pdf 渲染（不重复 UI 逻辑）
 *
 * 缓存：后端 OSS 缓存（md5(path)+mtime），同文件第二次预览毫秒级响应。
 * 失败：转换失败 → 显式错误 + 下载按钮（不再 fallback 静默下载）。
 */

import { useEffect, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import PreviewFrame from '../PreviewFrame';
import { getAuthHeaders } from '../../services/workspace';
import { API_BASE_URL } from '../../services/api';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const OFFICE_EXTS = new Set(['pptx', 'ppt', 'doc']);
const ZOOM_STEP = 0.2;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;

function PptxAdapterComponent({ item, onClose }: PreviewCommonProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 调后端转 PDF
  useEffect(() => {
    let cancelled = false;
    let currentBlobUrl: string | null = null;
    setLoading(true);
    setError(null);
    setBlobUrl(null);
    setPageNumber(1);

    (async () => {
      if (!item.workspacePath) {
        setError('文件缺少 workspace 路径，无法转换');
        setLoading(false);
        return;
      }
      try {
        const resp = await fetch(`${API_BASE_URL}/files/workspace/preview/render`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({ workspace_path: item.workspacePath }),
        });
        if (!resp.ok) {
          let msg = `转换失败 (HTTP ${resp.status})`;
          try {
            const data = await resp.json();
            msg = data?.detail?.message || data?.message || msg;
          } catch { /* ignore */ }
          throw new Error(msg);
        }
        const blob = await resp.blob();
        if (cancelled) return;
        currentBlobUrl = URL.createObjectURL(blob);
        setBlobUrl(currentBlobUrl);
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message);
        setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
    };
  }, [item]);

  // 翻页快捷键
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') setPageNumber((p) => Math.max(1, p - 1));
      else if (e.key === 'ArrowRight') {
        setPageNumber((p) => (numPages ? Math.min(numPages, p + 1) : p));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [numPages]);

  const footer = !loading && !error && numPages ? (
    <div className="flex items-center justify-center gap-3 px-4 py-2 bg-gray-900/90 text-white flex-shrink-0">
      <button
        onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
        disabled={pageNumber <= 1}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="上一页 (←)"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="tabular-nums text-sm">{pageNumber} / {numPages}</span>
      <button
        onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))}
        disabled={pageNumber >= numPages}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="下一页 (→)"
      >
        <ChevronRight size={18} />
      </button>
      <div className="w-px h-5 bg-white/20 mx-1" />
      <button
        onClick={() => setScale((s) => Math.max(MIN_ZOOM, +(s - ZOOM_STEP).toFixed(2)))}
        disabled={scale <= MIN_ZOOM}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="缩小"
      >
        <ZoomOut size={18} />
      </button>
      <span className="tabular-nums text-sm w-12 text-center">{Math.round(scale * 100)}%</span>
      <button
        onClick={() => setScale((s) => Math.min(MAX_ZOOM, +(s + ZOOM_STEP).toFixed(2)))}
        disabled={scale >= MAX_ZOOM}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="放大"
      >
        <ZoomIn size={18} />
      </button>
    </div>
  ) : null;

  return (
    <PreviewFrame item={item} onClose={onClose} loading={loading} error={error} footer={footer}>
      {blobUrl && (
        <div className="flex items-center justify-center min-h-full p-4">
          <Document
            file={blobUrl}
            onLoadSuccess={({ numPages: total }) => {
              setNumPages(total);
              setLoading(false);
            }}
            onLoadError={(e) => {
              setError(`PDF 渲染失败：${e.message}`);
              setLoading(false);
            }}
            loading={null}
            error={null}
          >
            <Page
              pageNumber={pageNumber}
              scale={scale}
              renderTextLayer
              renderAnnotationLayer
            />
          </Document>
        </div>
      )}
    </PreviewFrame>
  );
}

function matchPptx(item: PreviewItem): boolean {
  return OFFICE_EXTS.has(extOf(item.filename));
}

export const pptxAdapter: PreviewAdapter = {
  id: 'pptx',
  label: 'PowerPoint / Word（后端转 PDF）',
  priority: 80,
  match: matchPptx,
  Component: PptxAdapterComponent,
  supportsNavigation: false,
};
