/** Pure spreadsheet parsing helpers. */

interface WorksheetLike {
  '!merges'?: Array<{ s: { r: number; c: number }; e: { r: number; c: number } }>;
  [cell: string]: unknown;
}

function columnName(index: number): string {
  let name = '';
  let remaining = index;
  do {
    name = String.fromCharCode(65 + (remaining % 26)) + name;
    remaining = Math.floor(remaining / 26) - 1;
  } while (remaining >= 0);
  return name;
}

export function clearMergedCells(worksheet: WorksheetLike): void {
  const merges = worksheet['!merges'];
  if (!merges?.length) return;
  for (const range of merges) {
    for (let row = range.s.r; row <= range.e.r; row++) {
      for (let column = range.s.c; column <= range.e.c; column++) {
        if (row === range.s.r && column === range.s.c) continue;
        delete worksheet[`${columnName(column)}${row + 1}`];
      }
    }
  }
}

function readQuoted(text: string, index: number, cell: string) {
  const character = text[index];
  if (character === '"' && text[index + 1] === '"') {
    return { cell: `${cell}"`, inQuotes: true, nextIndex: index + 1 };
  }
  if (character === '"') return { cell, inQuotes: false, nextIndex: index };
  return { cell: cell + character, inQuotes: true, nextIndex: index };
}

function isRowBreak(text: string, index: number): boolean {
  return text[index] === '\n' || text[index] === '\r';
}

export function parseSpreadsheetCsv(text: string, separator: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (inQuotes) {
      const next = readQuoted(text, index, cell);
      ({ cell, inQuotes } = next);
      index = next.nextIndex;
    } else if (character === '"') {
      inQuotes = true;
    } else if (character === separator) {
      row.push(cell);
      cell = '';
    } else if (isRowBreak(text, index)) {
      row.push(cell);
      cell = '';
      if (row.some(Boolean)) rows.push(row);
      row = [];
      if (character === '\r' && text[index + 1] === '\n') index++;
    } else {
      cell += character;
    }
  }
  if (cell || row.length > 0) {
    row.push(cell);
    if (row.some(Boolean)) rows.push(row);
  }
  return rows;
}
