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
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import PreviewFrame from '../PreviewFrame';
import { getAuthHeaders } from '../../services/workspace';
import { API_BASE_URL } from '../../services/api';
import type { PreviewCommonProps } from '../types';
import PdfPreviewControls from './PdfPreviewControls';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

const ZOOM_STEP = 0.2;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;

type LoadStage = 'converting' | 'rendering' | 'done';

function getLoadingText(stage: LoadStage): string {
  return stage === 'converting'
    ? '正在转换文档为 PDF... 首次约需 3 秒，再次预览将秒开'
    : '正在渲染 PDF...';
}

export default function PptxPreview({ item, onClose }: PreviewCommonProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(true);
  const [stage, setStage] = useState<LoadStage>('converting');
  const [error, setError] = useState<string | null>(null);

  // 调后端转 PDF
  useEffect(() => {
    let cancelled = false;
    let currentBlobUrl: string | null = null;

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
        // 后端返回了 → 进入渲染阶段
        if (cancelled) return;
        setStage('rendering');
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
    <PdfPreviewControls
      pageNumber={pageNumber}
      numPages={numPages}
      scale={scale}
      minScale={MIN_ZOOM}
      maxScale={MAX_ZOOM}
      onPrevious={() => setPageNumber((p) => Math.max(1, p - 1))}
      onNext={() => setPageNumber((p) => Math.min(numPages, p + 1))}
      onZoomOut={() => setScale((s) => Math.max(MIN_ZOOM, +(s - ZOOM_STEP).toFixed(2)))}
      onZoomIn={() => setScale((s) => Math.min(MAX_ZOOM, +(s + ZOOM_STEP).toFixed(2)))}
    />
  ) : null;

  return (
    <PreviewFrame
      item={item}
      onClose={onClose}
      loading={loading}
      loadingText={getLoadingText(stage)}
      error={error}
      footer={footer}
    >
      {blobUrl && (
        <div className="flex items-center justify-center min-h-full p-4">
          <Document
            file={blobUrl}
            onLoadSuccess={({ numPages: total }) => {
              setNumPages(total);
              setStage('done');
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
