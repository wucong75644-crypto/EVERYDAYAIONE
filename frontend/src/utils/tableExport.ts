/**
 * 表格导出工具
 *
 * 从 Markdown 文本中提取表格数据，支持导出为 Excel（含嵌入图片）或 CSV。
 */

/** 图片 URL 域名匹配 */
const IMAGE_CDN_DOMAINS = ['img.alicdn.com', 'img.taobao.com', 'gw.alicdn.com'];
const IMAGE_URL_PATTERN = /^https?:\/\/.*\.(jpg|jpeg|png|webp|gif|bmp|svg)(\?.*)?$/i;

function isImageUrl(text: string): boolean {
  const trimmed = text.trim();
  if (IMAGE_URL_PATTERN.test(trimmed)) return true;
  try {
    const url = new URL(trimmed);
    return IMAGE_CDN_DOMAINS.some((d) => url.hostname.includes(d));
  } catch {
    return false;
  }
}

/** 从 Markdown 文本中提取所有表格 */
export function extractTables(markdown: string): string[][][] {
  const tables: string[][][] = [];
  const lines = markdown.split('\n');
  let currentTable: string[][] = [];
  let inTable = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      // 跳过分隔行（如 |---|---|）
      if (/^\|[\s\-:|]+\|$/.test(trimmed)) {
        inTable = true;
        continue;
      }
      const cells = trimmed
        .slice(1, -1)          // 去掉首尾 |
        .split('|')
        .map((c) => c.trim());
      currentTable.push(cells);
      inTable = true;
    } else {
      if (inTable && currentTable.length > 0) {
        tables.push(currentTable);
        currentTable = [];
      }
      inTable = false;
    }
  }
  // 处理文末表格
  if (currentTable.length > 0) {
    tables.push(currentTable);
  }
  return tables;
}

/** 检查 Markdown 文本是否包含表格 */
export function hasMarkdownTable(markdown: string): boolean {
  return extractTables(markdown).length > 0;
}

/** 下载图片为 ArrayBuffer（带超时） */
async function fetchImageBuffer(url: string, timeoutMs = 5000): Promise<ArrayBuffer | null> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return null;
    return await response.arrayBuffer();
  } catch {
    return null;
  }
}

/** 从 URL 推断图片扩展名 */
function getImageExtension(url: string): 'png' | 'jpeg' | 'gif' {
  const lower = url.toLowerCase();
  if (lower.includes('.png')) return 'png';
  if (lower.includes('.gif')) return 'gif';
  return 'jpeg';
}

/** 导出为 CSV 并下载 */
export function exportToCsv(table: string[][], filename: string): void {
  const csv = table
    .map((row) =>
      row.map((cell) => {
        // CSV 转义：含逗号/换行/引号的单元格用双引号包裹
        if (cell.includes(',') || cell.includes('\n') || cell.includes('"')) {
          return `"${cell.replace(/"/g, '""')}"`;
        }
        return cell;
      }).join(',')
    )
    .join('\n');

  const bom = '\uFEFF'; // UTF-8 BOM（Excel 打开不乱码）
  const blob = new Blob([bom + csv], { type: 'text/csv;charset=utf-8;' });
  downloadBlob(blob, `${filename}.csv`);
}

/** 导出为 Excel（含嵌入图片）并下载 */
export async function exportToExcel(
  table: string[][],
  filename: string,
  onProgress?: (current: number, total: number) => void,
): Promise<void> {
  const ExcelJS = await import('exceljs');
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet('Sheet1');

  // 收集所有需要下载的图片位置
  const imageJobs: { row: number; col: number; url: string }[] = [];

  // 写入数据
  table.forEach((row, rowIndex) => {
    const excelRow = sheet.addRow(row);

    // 样式：首行加粗
    if (rowIndex === 0) {
      excelRow.font = { bold: true };
      excelRow.fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFF3F4F6' },
      };
    }

    // 检测图片 URL
    row.forEach((cell, colIndex) => {
      if (isImageUrl(cell)) {
        imageJobs.push({ row: rowIndex + 1, col: colIndex + 1, url: cell });
      }
    });
  });

  // 设置列宽
  sheet.columns.forEach((col) => {
    col.width = 18;
  });

  // 下载并嵌入图片
  if (imageJobs.length > 0) {
    let completed = 0;
    for (const job of imageJobs) {
      onProgress?.(completed + 1, imageJobs.length);
      const buffer = await fetchImageBuffer(job.url);
      if (buffer) {
        const ext = getImageExtension(job.url);
        const imageId = workbook.addImage({
          buffer,
          extension: ext,
        });
        // 设置行高以容纳缩略图
        const excelRow = sheet.getRow(job.row + 1); // +1 因为 ExcelJS 行号从 1 开始
        excelRow.height = 45;

        sheet.addImage(imageId, {
          tl: { col: job.col - 1, row: job.row },
          ext: { width: 40, height: 40 },
        });
      }
      completed++;
    }
  }

  // 导出
  const buffer = await workbook.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  downloadBlob(blob, `${filename}.xlsx`);
}

/** 触发浏览器下载 */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
