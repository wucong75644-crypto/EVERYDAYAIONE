/**
 * 文件在线预览弹窗
 *
 * 支持：Excel/CSV 表格渲染、文本/代码预览（含行号）、PDF iframe 预览
 * 参考 ImagePreviewModal 的全屏弹窗架构。
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { createPortal } from 'react-dom';
import { X, Download, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { FilePart } from '../../../types/message';
import { downloadFile } from '../../../utils/downloadFile';
import { getFileIcon, formatFileSize } from '../../../utils/fileUtils';
import { getWorkspacePreviewUrl, getAuthHeaders } from '../../../services/workspace';

// ============================================================
// 常量
// ============================================================

/** 表格最多渲染行数 */
const MAX_TABLE_ROWS = 200;

const PREVIEWABLE_EXTS = new Set([
  'xlsx', 'xls', 'csv', 'tsv',
  'json', 'yaml', 'yml', 'xml',
  'txt', 'md', 'log',
  'py', 'js', 'ts', 'html', 'css', 'sql',
  'pdf',
]);

// ============================================================
// 合并单元格清理（预览保留原始空位，不做 ffill）
// ============================================================

/**
 * 清除 xlsx 解析后合并区域内非首行单元格的值。
 * xlsx 库会把合并单元格的值填充到区域内每个 cell，
 * 预览应只在首行显示，其余留空，与 Excel 视觉一致。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function clearMergedCells(ws: any): void {
  const merges: Array<{ s: { r: number; c: number }; e: { r: number; c: number } }> = ws['!merges'];
  if (!merges?.length) return;

  for (const range of merges) {
    for (let r = range.s.r; r <= range.e.r; r++) {
      for (let c = range.s.c; c <= range.e.c; c++) {
        // 跳过左上角首单元格（保留值）
        if (r === range.s.r && c === range.s.c) continue;
        // 列号转 A1 格式地址
        let col = '';
        let cc = c;
        do {
          col = String.fromCharCode(65 + (cc % 26)) + col;
          cc = Math.floor(cc / 26) - 1;
        } while (cc >= 0);
        delete ws[`${col}${r + 1}`];
      }
    }
  }
}

// ============================================================
// CSV 解析器（支持引号内逗号/换行）
// ============================================================

function parseCSV(text: string, separator: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === separator) {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
      row.push(cell);
      cell = '';
      if (row.some(Boolean)) rows.push(row);
      row = [];
      if (ch === '\r') i++;
    } else if (ch === '\r') {
      row.push(cell);
      cell = '';
      if (row.some(Boolean)) rows.push(row);
      row = [];
    } else {
      cell += ch;
    }
  }
  if (cell || row.length > 0) {
    row.push(cell);
    if (row.some(Boolean)) rows.push(row);
  }
  return rows;
}

// ============================================================
// 公共 API
// ============================================================

interface FilePreviewModalProps {
  file: FilePart;
  onClose: () => void;
}

/** 判断文件是否支持预览 */
export function canPreview(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return PREVIEWABLE_EXTS.has(ext);
}

// ============================================================
// 组件
// ============================================================

export default memo(function FilePreviewModal({ file, onClose }: FilePreviewModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tableData, setTableData] = useState<string[][] | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const workbookRef = useRef<any>(null);

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
        // PDF：iframe 原生渲染，直接用 URL（不走 fetch/blob，无大小限制）
        if (isPdf) {
          setPdfUrl(file.url || getWorkspacePreviewUrl(file.workspace_path!));
          setLoading(false);
          return;
        }

        // 非 PDF：CDN 优先 fetch，CORS 失败降级后端代理
        let response: Response;
        if (file.url) {
          try {
            response = await fetch(file.url);
            if (!response.ok) throw new Error(`CDN ${response.status}`);
          } catch {
            if (!file.workspace_path) throw new Error('加载失败');
            response = await fetch(getWorkspacePreviewUrl(file.workspace_path), { headers: getAuthHeaders() });
            if (!response.ok) throw new Error(`加载失败: ${response.status}`);
          }
        } else if (file.workspace_path) {
          response = await fetch(getWorkspacePreviewUrl(file.workspace_path), { headers: getAuthHeaders() });
          if (!response.ok) throw new Error(`加载失败: ${response.status}`);
        } else {
          throw new Error('无可用的文件 URL');
        }

        if (cancelled) return;

        if (isExcel) {
          const { read, utils } = await import('xlsx');
          const buffer = await response.arrayBuffer();
          if (cancelled) return;
          // sheetRows: 只解析前 N 行（预览不需要全量数据，56MB 文件秒开）
          const wb = read(buffer, { sheetRows: MAX_TABLE_ROWS + 1 });
          workbookRef.current = wb;
          setSheetNames(wb.SheetNames);
          const ws = wb.Sheets[wb.SheetNames[0]];
          clearMergedCells(ws);
          setTableData(utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' }) as string[][]);
        } else if (isCsv) {
          const text = await response.text();
          if (cancelled) return;
          const separator = ext === 'tsv' ? '\t' : ',';
          setTableData(parseCSV(text, separator));
        } else {
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
  }, [file.url, file.workspace_path, isPdf, isExcel, isCsv, ext]);

  // Sheet 切换（workbook 已用 sheetRows 限制过解析深度，直接读取缓存）
  const handleSheetChange = useCallback(async (index: number) => {
    setActiveSheet(index);
    const wb = workbookRef.current;
    if (!wb) return;
    try {
      const { utils } = await import('xlsx');
      const ws = wb.Sheets[wb.SheetNames[index]];
      clearMergedCells(ws);
      const rows = utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' }) as string[][];
      setTableData(rows);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const handleDownload = async () => {
    try {
      await downloadFile(file.url || getWorkspacePreviewUrl(file.workspace_path!), file.name);
    } catch {
      toast.error('下载失败');
    }
  };

  // 表格数据行数（不含表头）
  const dataRowCount = tableData ? tableData.length - 1 : 0;
  const displayRows = tableData ? tableData.slice(1, 1 + MAX_TABLE_ROWS) : [];

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-black/80">
      {/* 点击遮罩关闭（独立层，不影响内容区点击） */}
      <div className="absolute inset-0 -z-10" onClick={onClose} />

      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-6 py-3 bg-gray-900/90 text-white flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-lg">{getFileIcon(file.name)}</span>
          <span className="truncate font-medium">{file.name}</span>
          {file.size != null && (
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
      <div className="flex-1 overflow-auto min-h-0">
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

        {/* PDF 预览（iframe 原生渲染，浏览器流式加载，无大小限制） */}
        {isPdf && pdfUrl && !loading && (
          <iframe
            src={pdfUrl}
            className="w-full h-full bg-white"
            title={file.name}
          />
        )}

        {/* 表格预览（Excel/CSV） */}
        {tableData && !loading && (
          <div className="p-4">
            {dataRowCount === 0 ? (
              <div className="flex items-center justify-center py-12 text-gray-400 bg-white dark:bg-gray-900 rounded-lg">
                暂无数据
              </div>
            ) : (
              <div className="overflow-auto rounded-lg bg-white dark:bg-gray-900 max-h-[calc(100vh-140px)]">
                <table className="text-sm border-collapse">
                  <thead>
                    {tableData.length > 0 && (
                      <tr>
                        {/* 行号列 */}
                        <th className="px-2 py-2 text-center text-xs font-normal bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 border-b border-r border-gray-300 dark:border-gray-600 sticky top-0 z-10 w-12">
                          #
                        </th>
                        {tableData[0].map((cell, i) => (
                          <th
                            key={i}
                            className="px-3 py-2 text-left font-semibold bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 whitespace-nowrap max-w-[240px] truncate sticky top-0 z-10"
                            title={String(cell ?? '')}
                          >
                            {cell ?? ''}
                          </th>
                        ))}
                      </tr>
                    )}
                  </thead>
                  <tbody>
                    {displayRows.map((row, ri) => (
                      <tr key={ri} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                        {/* 行号 */}
                        <td className="px-2 py-1.5 text-center text-xs bg-gray-50 dark:bg-gray-850 text-gray-400 border-b border-r border-gray-200 dark:border-gray-700 sticky left-0">
                          {ri + 1}
                        </td>
                        {row.map((cell, ci) => (
                          <td
                            key={ci}
                            className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-800 text-gray-700 dark:text-gray-300 whitespace-nowrap max-w-[240px] truncate"
                            title={String(cell ?? '')}
                          >
                            {cell ?? ''}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {dataRowCount >= MAX_TABLE_ROWS && (
                  <div className="px-4 py-2 text-sm text-gray-500 bg-gray-50 dark:bg-gray-800 sticky bottom-0">
                    仅显示前 {MAX_TABLE_ROWS} 行，下载文件查看完整数据
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 文本/代码预览（含行号） */}
        {textContent !== null && !loading && (
          <div className="p-4">
            <div className="rounded-lg bg-gray-900 overflow-auto max-h-[calc(100vh-140px)]">
              <table className="text-sm leading-6 w-full">
                <tbody>
                  {textContent.split('\n').map((line, i) => (
                    <tr key={i} className="hover:bg-gray-800/50">
                      <td className="pl-4 pr-3 py-0 text-right text-gray-500 select-none w-12 align-top sticky left-0 bg-gray-900">
                        {i + 1}
                      </td>
                      <td className="pr-4 py-0 text-gray-100 whitespace-pre-wrap break-all">
                        {line || '\u00A0'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* 底部 Sheet 切换（Excel 多 Sheet） */}
      {sheetNames.length > 1 && (
        <div className="flex items-center gap-1 px-4 py-2 bg-gray-900/90 overflow-x-auto flex-shrink-0">
          {sheetNames.map((name, i) => (
            <button
              key={name}
              onClick={() => handleSheetChange(i)}
              className={`px-3 py-1 rounded text-sm transition-colors whitespace-nowrap flex-shrink-0 ${
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
