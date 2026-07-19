import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';

interface PdfPreviewControlsProps {
  pageNumber: number;
  numPages: number;
  scale: number;
  minScale: number;
  maxScale: number;
  onPrevious: () => void;
  onNext: () => void;
  onZoomOut: () => void;
  onZoomIn: () => void;
}

export default function PdfPreviewControls({
  pageNumber,
  numPages,
  scale,
  minScale,
  maxScale,
  onPrevious,
  onNext,
  onZoomOut,
  onZoomIn,
}: PdfPreviewControlsProps) {
  return (
    <div className="flex items-center justify-center gap-3 px-4 py-2 bg-gray-900/90 text-white flex-shrink-0">
      <button
        onClick={onPrevious}
        disabled={pageNumber <= 1}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="上一页 (←)"
        aria-label="上一页"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="tabular-nums text-sm">{pageNumber} / {numPages}</span>
      <button
        onClick={onNext}
        disabled={pageNumber >= numPages}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="下一页 (→)"
        aria-label="下一页"
      >
        <ChevronRight size={18} />
      </button>
      <div className="w-px h-5 bg-white/20 mx-1" />
      <button
        onClick={onZoomOut}
        disabled={scale <= minScale}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="缩小"
        aria-label="缩小"
      >
        <ZoomOut size={18} />
      </button>
      <span className="tabular-nums text-sm w-12 text-center">{Math.round(scale * 100)}%</span>
      <button
        onClick={onZoomIn}
        disabled={scale >= maxScale}
        className="p-2 rounded hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
        title="放大"
        aria-label="放大"
      >
        <ZoomIn size={18} />
      </button>
    </div>
  );
}
