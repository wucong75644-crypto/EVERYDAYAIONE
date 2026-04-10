/**
 * 表格导出弹窗
 *
 * 用户选择导出格式（Excel/CSV）并输入文件名后导出。
 * 支持 Excel 导出时显示图片下载进度。
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { extractTables, exportToCsv, exportToExcel } from '../../../utils/tableExport';

interface TableExportModalProps {
  /** 消息的 Markdown 文本内容 */
  markdownContent: string;
  /** 关闭回调 */
  onClose: () => void;
}

type ExportFormat = 'excel' | 'csv';

/** 生成默认文件名 */
function getDefaultFilename(): string {
  const now = new Date();
  const date = now.toISOString().slice(0, 10);
  return `表格导出_${date}`;
}

export default function TableExportModal({
  markdownContent,
  onClose,
}: TableExportModalProps) {
  const [format, setFormat] = useState<ExportFormat>('excel');
  const [filename, setFilename] = useState(getDefaultFilename);
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  // 自动聚焦输入框
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  const handleExport = useCallback(async () => {
    const tables = extractTables(markdownContent);
    if (tables.length === 0) return;

    const name = filename.trim() || getDefaultFilename();
    // 如果有多个表格，合并为一个（首行为表头，后续表格跳过重复表头）
    let mergedTable = tables[0];
    for (let i = 1; i < tables.length; i++) {
      mergedTable = [...mergedTable, ...tables[i].slice(1)];
    }

    setExporting(true);
    try {
      if (format === 'csv') {
        exportToCsv(mergedTable, name);
      } else {
        await exportToExcel(mergedTable, name, (current, total) => {
          setProgress(`正在下载图片 ${current}/${total}...`);
        });
      }
      onClose();
    } catch {
      const toast = (await import('react-hot-toast')).default;
      toast.error('导出失败，请重试');
    } finally {
      setExporting(false);
      setProgress('');
    }
  }, [markdownContent, filename, format, onClose]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !exporting) {
        handleExport();
      }
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [handleExport, onClose, exporting],
  );

  return (
    <div
      className="absolute bottom-full right-0 mb-1.5 bg-surface-card rounded-xl shadow-xl border border-border-default p-4 w-72 z-30 animate-popup-enter"
      onKeyDown={handleKeyDown}
    >
      <h3 className="text-sm font-medium text-text-primary mb-3">导出表格</h3>

      {/* 格式选择 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setFormat('excel')}
          className={`flex-1 px-3 py-1.5 text-xs rounded-lg border transition-base ${
            format === 'excel'
              ? 'border-accent bg-accent-light text-accent'
              : 'border-border-default text-text-tertiary hover:bg-hover'
          }`}
        >
          Excel (.xlsx)
        </button>
        <button
          onClick={() => setFormat('csv')}
          className={`flex-1 px-3 py-1.5 text-xs rounded-lg border transition-base ${
            format === 'csv'
              ? 'border-accent bg-accent-light text-accent'
              : 'border-border-default text-text-tertiary hover:bg-hover'
          }`}
        >
          CSV (.csv)
        </button>
      </div>

      {/* 文件名输入 */}
      <div className="mb-3">
        <input
          ref={inputRef}
          type="text"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          placeholder="文件名"
          className="w-full px-3 py-1.5 text-sm bg-surface-card text-text-primary border border-border-default rounded-lg focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-transparent"
          disabled={exporting}
        />
      </div>

      {/* 进度提示 */}
      {progress && (
        <p className="text-xs text-text-tertiary mb-2">{progress}</p>
      )}

      {/* 操作按钮 */}
      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          disabled={exporting}
          className="px-3 py-1.5 text-xs text-text-secondary hover:bg-hover rounded-lg transition-base disabled:opacity-50"
        >
          取消
        </button>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="px-3 py-1.5 text-xs text-text-on-accent bg-accent hover:bg-accent-hover rounded-lg transition-base disabled:opacity-50 flex items-center gap-1"
        >
          {exporting ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" />
              导出中
            </>
          ) : (
            '导出'
          )}
        </button>
      </div>
    </div>
  );
}
