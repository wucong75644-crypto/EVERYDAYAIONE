/**
 * 文件在线预览弹窗
 *
 * 支持：Excel/CSV 表格渲染、文本/代码高亮、PDF iframe 预览
 * 参考 ImagePreviewModal 的全屏弹窗架构。
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { X, Download, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { FilePart } from '../../types/message';
import { downloadFile } from '../../utils/downloadFile';
import { getFileIcon, formatFileSize } from '../../utils/fileUtils';

interface FilePreviewModalProps {
  file: FilePart;
  onClose: () => void;
}

/** 判断文件是否支持预览 */
export function canPreview(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return PREVIEWABLE_EXTS.has(ext);
}

const PREVIEWABLE_EXTS = new Set([
  'xlsx', 'xls', 'csv', 'tsv',
  'json', 'yaml', 'yml', 'xml',
  'txt', 'md', 'log',
  'py', 'js', 'ts', 'html', 'css', 'sql',
  'pdf',
]);

export default memo(function FilePreviewModal({ file, onClose }: FilePreviewModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tableData, setTableData] = useState<string[][] | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const workbookRef = useRef<any>(null);  // 缓存 xlsx workbook，避免 sheet 切换重复 fetch

  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const isPdf = ext === 'pdf';
  const isExcel = ['xlsx', 'xls'].includes(ext);
  const isCsv = ['csv', 'tsv'].includes(ext);

  // ESC 关闭
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  // 加载文件内容
  useEffect(() => {
    let cancelled = false;

    async function loadContent() {
      setLoading(true);
      setError(null);

      try {
        if (isPdf) {
          // PDF 用 iframe，不需要预加载
          setLoading(false);
          return;
        }

        const response = await fetch(file.url, { mode: 'cors', credentials: 'omit' });
        if (!response.ok) throw new Error(`加载失败: ${response.status}`);

        if (isExcel) {
          const { read, utils } = await import('xlsx');
          const buffer = await response.arrayBuffer();
          const wb = read(buffer);
          if (cancelled) return;
          workbookRef.current = wb;  // 缓存 workbook
          setSheetNames(wb.SheetNames);
          const ws = wb.Sheets[wb.SheetNames[0]];
          setTableData(utils.sheet_to_json<string[]>(ws, { header: 1 }) as string[][]);
        } else if (isCsv) {
          const text = await response.text();
          if (cancelled) return;
          const separator = ext === 'tsv' ? '\t' : ',';
          const rows = text.split('\n').map((row) => row.split(separator));
          setTableData(rows);
        } else {
          // 文本/代码
          const text = await response.text();
          if (cancelled) return;
          setTextContent(text);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadContent();
    return () => { cancelled = true; };
  }, [file.url, isPdf, isExcel, isCsv, ext]);

  // 切换 Sheet（从缓存读取，无需重新 fetch）
  const handleSheetChange = useCallback(async (index: number) => {
    setActiveSheet(index);
    const wb = workbookRef.current;
    if (!wb) return;
    try {
      const { utils } = await import('xlsx');
      const ws = wb.Sheets[wb.SheetNames[index]];
      setTableData(utils.sheet_to_json<string[]>(ws, { header: 1 }) as string[][]);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const handleDownload = async () => {
    try {
      await downloadFile(file.url, file.name);
    } catch {
      toast.error('下载失败');
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/80"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-6 py-3 bg-gray-900/90 text-white">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-lg">{getFileIcon(file.name)}</span>
          <span className="truncate font-medium">{file.name}</span>
          {file.size && (
            <span className="text-sm text-gray-400 flex-shrink-0">
              {formatFileSize(file.size)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDownload}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            title="下载"
          >
            <Download size={20} />
          </button>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            title="关闭"
          >
            <X size={20} />
          </button>
        </div>
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-auto">
        {loading && (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-8 h-8 animate-spin text-white" />
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center h-full text-red-400">
            {error}
          </div>
        )}

        {/* PDF 预览 */}
        {isPdf && !loading && (
          <iframe
            src={file.url}
            className="w-full h-full bg-white"
            title={file.name}
          />
        )}

        {/* 表格预览（Excel/CSV） */}
        {tableData && !loading && (
          <div className="p-4">
            <div className="overflow-auto rounded-lg bg-white dark:bg-gray-900">
              <table className="min-w-full text-sm">
                <thead>
                  {tableData.length > 0 && (
                    <tr>
                      {tableData[0].map((cell, i) => (
                        <th
                          key={i}
                          className="px-4 py-2 text-left font-semibold bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 whitespace-nowrap"
                        >
                          {cell ?? ''}
                        </th>
                      ))}
                    </tr>
                  )}
                </thead>
                <tbody>
                  {tableData.slice(1, 200).map((row, ri) => (
                    <tr key={ri} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                      {row.map((cell, ci) => (
                        <td
                          key={ci}
                          className="px-4 py-1.5 border-b border-gray-100 dark:border-gray-800 text-gray-700 dark:text-gray-300 whitespace-nowrap"
                        >
                          {cell ?? ''}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {tableData.length > 201 && (
                <div className="px-4 py-2 text-sm text-gray-500 bg-gray-50 dark:bg-gray-800">
                  显示前 200 行，共 {tableData.length - 1} 行数据
                </div>
              )}
            </div>
          </div>
        )}

        {/* 文本/代码预览 */}
        {textContent !== null && !loading && (
          <div className="p-4">
            <pre className="p-4 rounded-lg bg-gray-900 text-gray-100 text-sm overflow-auto max-h-[80vh] whitespace-pre-wrap">
              {textContent}
            </pre>
          </div>
        )}
      </div>

      {/* 底部 Sheet 切换（Excel 多 Sheet） */}
      {sheetNames.length > 1 && (
        <div className="flex items-center gap-1 px-4 py-2 bg-gray-900/90">
          {sheetNames.map((name, i) => (
            <button
              key={name}
              onClick={() => handleSheetChange(i)}
              className={`px-3 py-1 rounded text-sm transition-colors ${
                i === activeSheet
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>,
    document.body,
  );
});


