/** Presentational spreadsheet table and sheet tabs. */

import { formatDisplayValue } from '../../utils/displayValue';

const MAX_TABLE_ROWS = 200;

export function SpreadsheetSheetTabs({
  names,
  active,
  onChange,
}: {
  names: string[];
  active: number;
  onChange: (index: number) => void;
}) {
  if (names.length <= 1) return null;
  return (
    <div className="flex items-center gap-1 px-4 py-2 bg-gray-900/90 overflow-x-auto flex-shrink-0">
      {names.map((name, index) => (
        <button key={name} onClick={() => onChange(index)}
          className={`px-3 py-1 rounded text-sm transition-colors whitespace-nowrap flex-shrink-0 ${
            index === active ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
          }`}>
          {name}
        </button>
      ))}
    </div>
  );
}

export function SpreadsheetTable({ data }: { data: unknown[][] }) {
  const dataRowCount = data.length - 1;
  if (dataRowCount === 0) {
    return (
      <div className="flex items-center justify-center py-12 text-gray-400 bg-white dark:bg-gray-900 rounded-lg">
        暂无数据
      </div>
    );
  }
  const displayRows = data.slice(1, 1 + MAX_TABLE_ROWS);
  return (
    <div className="overflow-auto rounded-lg bg-white dark:bg-gray-900 max-h-[calc(100vh-140px)]">
      <table className="text-sm border-collapse">
        <thead><tr>
          <th className="px-2 py-2 text-center text-xs font-normal bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 border-b border-r border-gray-300 dark:border-gray-600 sticky top-0 z-10 w-12">#</th>
          {data[0].map((cell, index) => (
            <th key={index} title={formatDisplayValue(cell)}
              className="px-3 py-2 text-left font-semibold bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 whitespace-nowrap max-w-[240px] truncate sticky top-0 z-10">
              {formatDisplayValue(cell)}
            </th>
          ))}
        </tr></thead>
        <tbody>{displayRows.map((row, rowIndex) => (
          <tr key={rowIndex} className="hover:bg-gray-50 dark:hover:bg-gray-800">
            <td className="px-2 py-1.5 text-center text-xs bg-gray-50 dark:bg-gray-850 text-gray-400 border-b border-r border-gray-200 dark:border-gray-700 sticky left-0">{rowIndex + 1}</td>
            {row.map((cell, cellIndex) => (
              <td key={cellIndex} title={formatDisplayValue(cell)}
                className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-800 text-gray-700 dark:text-gray-300 whitespace-nowrap max-w-[240px] truncate">
                {formatDisplayValue(cell)}
              </td>
            ))}
          </tr>
        ))}</tbody>
      </table>
      {dataRowCount >= MAX_TABLE_ROWS && (
        <div className="px-4 py-2 text-sm text-gray-500 bg-gray-50 dark:bg-gray-800 sticky bottom-0">
          仅显示前 {MAX_TABLE_ROWS} 行，下载文件查看完整数据
        </div>
      )}
    </div>
  );
}
