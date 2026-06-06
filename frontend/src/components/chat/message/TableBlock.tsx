/**
 * 交互式表格内容块(沙盒 emit_table 触发)
 *
 * 设计:简洁默认 + 列对齐(数字右对齐) + 截断提示 + 行 hover
 * 不引入额外依赖,纯 HTML table + Tailwind
 */
import { memo, useMemo } from 'react';

interface TableBlockProps {
  title?: string;
  columns: string[];
  rows: Record<string, unknown>[];
  truncated?: boolean;
}

// 注意:后端 services/sandbox/emit_protocol.py:_TABLE_MAX_ROWS 必须保持一致
const MAX_PREVIEW_ROWS = 200;

function isNumeric(value: unknown): boolean {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value === 'string') {
    const n = Number(value);
    return Number.isFinite(n) && value.trim() !== '';
  }
  return false;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const TableBlockComponent = ({ title, columns, rows, truncated }: TableBlockProps) => {
  // 判断每列是否数字列(用于对齐)
  const numericCols = useMemo(() => {
    const flags: Record<string, boolean> = {};
    for (const col of columns) {
      // 前 10 行采样
      const sample = rows.slice(0, 10).map((r) => r[col]).filter((v) => v !== null && v !== undefined);
      flags[col] = sample.length > 0 && sample.every(isNumeric);
    }
    return flags;
  }, [columns, rows]);

  const displayRows = rows.slice(0, MAX_PREVIEW_ROWS);

  return (
    <div className="my-2 rounded-md border border-base-300 bg-base-100 overflow-hidden">
      {title && (
        <div className="px-3 py-2 border-b border-base-300 text-sm font-medium text-base-content">
          {title}
        </div>
      )}
      <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-base-200">
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  className={`px-3 py-1.5 font-medium text-base-content/70 border-b border-base-300 whitespace-nowrap ${
                    numericCols[col] ? 'text-right' : 'text-left'
                  }`}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row, i) => (
              <tr
                key={i}
                className="border-b border-base-200 last:border-b-0 hover:bg-base-200/40"
              >
                {columns.map((col) => (
                  <td
                    key={col}
                    className={`px-3 py-1.5 text-base-content/90 whitespace-nowrap ${
                      numericCols[col] ? 'text-right tabular-nums' : 'text-left'
                    }`}
                  >
                    {formatCell(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1.5 text-xs text-base-content/50 bg-base-200/40 border-t border-base-300 flex items-center justify-between">
        <span>共 {rows.length} 行 × {columns.length} 列</span>
        {(truncated || rows.length > MAX_PREVIEW_ROWS) && (
          <span className="text-warning">(预览前 {Math.min(MAX_PREVIEW_ROWS, rows.length)} 行)</span>
        )}
      </div>
    </div>
  );
};

export const TableBlock = memo(TableBlockComponent);
