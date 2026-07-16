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

import type { PreviewAdapter, PreviewItem } from '../types';
import { extOf } from '../types';
import { SpreadsheetPreview } from './SpreadsheetPreview';

const SHEET_EXTS = new Set(['xlsx', 'xls']);
const CSV_EXTS = new Set(['csv', 'tsv']);

function matchSpreadsheet(item: PreviewItem): boolean {
  const ext = extOf(item.filename);
  return SHEET_EXTS.has(ext) || CSV_EXTS.has(ext);
}

export const spreadsheetAdapter: PreviewAdapter = {
  id: 'spreadsheet',
  label: '电子表格',
  priority: 80,
  match: matchSpreadsheet,
  Component: SpreadsheetPreview,
  supportsNavigation: false,
};
