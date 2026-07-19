/**
 * PdfAdapter — PDF 预览（PDF.js 自渲染）
 *
 * 为什么不用 iframe：浏览器内置 PDF 查看器被禁用 / Safari iOS / 隐私扩展
 * 等场景下，`<iframe src="*.pdf">` 会黑屏 + 自动触发下载。改用 react-pdf
 * （Mozilla PDF.js 的 React 封装）在前端 canvas 自渲染，行为可控。
 *
 * CDN 流量节省策略保留：react-pdf 通过 fetch CDN URL 拿 PDF buffer 后本地解析，
 * 流量仍走 CDN。
 *
 * 提供：翻页（← →）/ 缩放（+/-）/ 失败兜底（onError → 显式下载按钮）
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import PreviewFrame from '../PreviewFrame';
import { resolvePreviewUrl } from '../fetchPreview';
import type { PreviewCommonProps } from '../types';
import PdfPreviewControls from './PdfPreviewControls';

// 配置 PDF.js worker — 用 vite 的 ?url 让 worker 单独打包，
// 不依赖 CDN URL（防止 PDF.js CDN 不稳定）
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const ZOOM_STEP = 0.2;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;

export default function PdfPreview({ item, onClose }: PreviewCommonProps) {
  const pdfUrl = useMemo(() => resolvePreviewUrl(item), [item]);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const onDocLoadSuccess = useCallback(({ numPages: total }: { numPages: number }) => {
    setNumPages(total);
    setLoading(false);
  }, []);

  const onDocLoadError = useCallback((e: Error) => {
    setError(`PDF 加载失败：${e.message}`);
    setLoading(false);
  }, []);

  // PDF.js 加载进度回调
  const onDocLoadProgress = useCallback(({ loaded, total }: { loaded: number; total?: number }) => {
    if (total && total > 0) {
      setLoadProgress(Math.min(99, Math.round((loaded / total) * 100)));
    }
  }, []);

  const loadingText = loadProgress > 0
    ? `加载 PDF 中... ${loadProgress}%`
    : '加载 PDF 中...';

  const prev = useCallback(() => setPageNumber((p) => Math.max(1, p - 1)), []);
  const next = useCallback(
    () => setPageNumber((p) => (numPages ? Math.min(numPages, p + 1) : p)),
    [numPages],
  );
  const zoomIn = useCallback(() => setScale((s) => Math.min(MAX_ZOOM, +(s + ZOOM_STEP).toFixed(2))), []);
  const zoomOut = useCallback(() => setScale((s) => Math.max(MIN_ZOOM, +(s - ZOOM_STEP).toFixed(2))), []);

  // 键盘：← → 翻页（在 PreviewFrame ESC 监听之外补充）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') prev();
      else if (e.key === 'ArrowRight') next();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [prev, next]);

  if (!pdfUrl) {
    return (
      <PreviewFrame item={item} onClose={onClose} error="文件无可用 URL" />
    );
  }

  // 自定义底部工具栏（页码 + 翻页 + 缩放）
  const footer = !loading && !error && numPages ? (
    <PdfPreviewControls
      pageNumber={pageNumber}
      numPages={numPages}
      scale={scale}
      minScale={MIN_ZOOM}
      maxScale={MAX_ZOOM}
      onPrevious={prev}
      onNext={next}
      onZoomOut={zoomOut}
      onZoomIn={zoomIn}
    />
  ) : null;

  return (
    <PreviewFrame
      item={item}
      onClose={onClose}
      loading={loading}
      loadingText={loadingText}
      error={error}
      footer={footer}
    >
      <div className="flex items-center justify-center min-h-full p-4">
        <Document
          file={pdfUrl}
          onLoadSuccess={onDocLoadSuccess}
          onLoadError={onDocLoadError}
          onLoadProgress={onDocLoadProgress}
          loading={null}
          error={null}
        >
          <Page
            pageNumber={pageNumber}
            scale={scale}
            renderTextLayer={true}
            renderAnnotationLayer={true}
          />
        </Document>
      </div>
    </PreviewFrame>
  );
}
