/**
 * 表格导出弹窗
 *
 * 用户选择导出格式（Excel/CSV）并输入文件名后导出。
 * 支持 Excel 导出时显示图片下载进度。
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { extractTables, exportToCsv, exportToExcel } from '../../utils/tableExport';

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
      className="absolute bottom-full right-0 mb-1.5 bg-white rounded-xl shadow-xl border border-gray-200 p-4 w-72 z-20 animate-popupEnter"
      onKeyDown={handleKeyDown}
    >
      <h3 className="text-sm font-medium text-gray-900 mb-3">导出表格</h3>

      {/* 格式选择 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setFormat('excel')}
          className={`flex-1 px-3 py-1.5 text-xs rounded-lg border transition-colors ${
            format === 'excel'
              ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
              : 'border-gray-200 text-gray-600 hover:bg-gray-50'
          }`}
        >
          Excel (.xlsx)
        </button>
        <button
          onClick={() => setFormat('csv')}
          className={`flex-1 px-3 py-1.5 text-xs rounded-lg border transition-colors ${
            format === 'csv'
              ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
              : 'border-gray-200 text-gray-600 hover:bg-gray-50'
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
          className="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
          disabled={exporting}
        />
      </div>

      {/* 进度提示 */}
      {progress && (
        <p className="text-xs text-gray-500 mb-2">{progress}</p>
      )}

      {/* 操作按钮 */}
      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          disabled={exporting}
          className="px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
        >
          取消
        </button>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="px-3 py-1.5 text-xs text-white bg-indigo-500 hover:bg-indigo-600 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1"
        >
          {exporting ? (
            <>
              <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
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
