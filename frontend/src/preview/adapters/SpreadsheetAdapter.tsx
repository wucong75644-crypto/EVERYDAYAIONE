/**
 * SpreadsheetAdapter — Excel / CSV / TSV 预览
 *
 * 1:1 复刻原 FilePreviewModal.tsx 的所有表格能力：
 *   - xlsx.read(buffer, { sheetRows: 201 })  大文件秒开（限制深度解析）
 *   - workbookRef 缓存 + 多 Sheet 底部 tab 切换
 *   - clearMergedCells 合并单元格清非首行（避免值重复）
 *   - parseCSV 自定义解析器（引号内逗号/换行）
 *   - MAX_TABLE_ROWS=200 截断 + 提示
 *   - 行号 sticky-left + 表头 sticky-top + max-w-[240px] truncate + title=完整内容
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import PreviewFrame from '../PreviewFrame';
import { fetchPreviewResponse } from '../fetchPreview';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';

const SHEET_EXTS = new Set(['xlsx', 'xls']);
const CSV_EXTS = new Set(['csv', 'tsv']);
const MAX_TABLE_ROWS = 200;

// ============================================================
// 合并单元格清理（迁移自 FilePreviewModal.clearMergedCells）
//
// xlsx 库会把合并单元格的值填充到区域内每个 cell，
// 预览应只在首行显示，其余留空，与 Excel 视觉一致。
// ============================================================
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function clearMergedCells(ws: any): void {
  const merges: Array<{ s: { r: number; c: number }; e: { r: number; c: number } }> = ws['!merges'];
  if (!merges?.length) return;

  for (const range of merges) {
    for (let r = range.s.r; r <= range.e.r; r++) {
      for (let c = range.s.c; c <= range.e.c; c++) {
        if (r === range.s.r && c === range.s.c) continue;
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
// CSV 解析器（迁移自 FilePreviewModal.parseCSV）
// 支持引号内逗号/换行
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
// 组件
// ============================================================

function SpreadsheetAdapterComponent({ item, onClose }: PreviewCommonProps) {
  const ext = extOf(item.filename);
  const isExcel = SHEET_EXTS.has(ext);
  const isCsv = CSV_EXTS.has(ext);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tableData, setTableData] = useState<string[][] | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const workbookRef = useRef<any>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    workbookRef.current = null;

    (async () => {
      try {
        const { response } = await fetchPreviewResponse(item);

        if (isExcel) {
          const { read, utils } = await import('xlsx');
          const buffer = await response.arrayBuffer();
          if (cancelled) return;
          // sheetRows: 只解析前 N 行，56MB 文件秒开
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
        }
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message || '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [item, ext, isExcel, isCsv]);

  // Sheet 切换：从 workbookRef 缓存解析
  const handleSheetChange = useCallback(async (i: number) => {
    setActiveSheet(i);
    const wb = workbookRef.current;
    if (!wb) return;
    try {
      const { utils } = await import('xlsx');
      const ws = wb.Sheets[wb.SheetNames[i]];
      clearMergedCells(ws);
      const rows = utils.sheet_to_json<string[]>(ws, { header: 1, defval: '' }) as string[][];
      setTableData(rows);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const dataRowCount = tableData ? tableData.length - 1 : 0;
  const displayRows = tableData ? tableData.slice(1, 1 + MAX_TABLE_ROWS) : [];

  // 底部 Sheet tab（多 Sheet 时显示）
  const footer = sheetNames.length > 1 ? (
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
  ) : null;

  return (
    <PreviewFrame item={item} onClose={onClose} loading={loading} error={error} footer={footer}>
      {tableData && (
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
    </PreviewFrame>
  );
}

function matchSpreadsheet(item: PreviewItem): boolean {
  const ext = extOf(item.filename);
  return SHEET_EXTS.has(ext) || CSV_EXTS.has(ext);
}

export const spreadsheetAdapter: PreviewAdapter = {
  id: 'spreadsheet',
  label: '电子表格',
  priority: 80,
  match: matchSpreadsheet,
  Component: SpreadsheetAdapterComponent,
  supportsNavigation: false,
};
