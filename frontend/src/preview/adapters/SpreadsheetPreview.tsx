import { useCallback, useEffect, useRef, useState } from 'react';
import type { WorkBook } from 'xlsx';
import PreviewFrame from '../PreviewFrame';
import { fetchPreviewResponse } from '../fetchPreview';
import type { PreviewCommonProps } from '../types';
import { extOf } from '../types';
import { clearMergedCells, parseSpreadsheetCsv } from './spreadsheetData';
import { SpreadsheetSheetTabs, SpreadsheetTable } from './SpreadsheetTable';

const MAX_TABLE_ROWS = 200;

export function SpreadsheetPreview({ item, onClose }: PreviewCommonProps) {
  const extension = extOf(item.filename);
  const isExcel = extension === 'xlsx' || extension === 'xls';
  const isCsv = extension === 'csv' || extension === 'tsv';
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tableData, setTableData] = useState<unknown[][] | null>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const workbookRef = useRef<WorkBook | null>(null);

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
          const workbook = read(buffer, { sheetRows: MAX_TABLE_ROWS + 1 });
          workbookRef.current = workbook;
          setSheetNames(workbook.SheetNames);
          const worksheet = workbook.Sheets[workbook.SheetNames[0]];
          clearMergedCells(worksheet);
          setTableData(utils.sheet_to_json<unknown[]>(worksheet, { header: 1, defval: '' }));
        } else if (isCsv) {
          const text = await response.text();
          if (cancelled) return;
          setTableData(parseSpreadsheetCsv(text, extension === 'tsv' ? '\t' : ','));
        }
      } catch (caught) {
        if (!cancelled) setError((caught as Error).message || '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [item, extension, isExcel, isCsv]);

  const handleSheetChange = useCallback(async (index: number) => {
    setActiveSheet(index);
    const workbook = workbookRef.current;
    if (!workbook) return;
    try {
      const { utils } = await import('xlsx');
      const worksheet = workbook.Sheets[workbook.SheetNames[index]];
      clearMergedCells(worksheet);
      setTableData(utils.sheet_to_json<unknown[]>(worksheet, { header: 1, defval: '' }));
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, []);

  const footer = (
    <SpreadsheetSheetTabs names={sheetNames} active={activeSheet} onChange={handleSheetChange} />
  );
  return (
    <PreviewFrame item={item} onClose={onClose} loading={loading} error={error} footer={footer}>
      {tableData && <div className="p-4"><SpreadsheetTable data={tableData} /></div>}
    </PreviewFrame>
  );
}
