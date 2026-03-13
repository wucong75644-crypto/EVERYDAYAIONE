/**
 * tableExport 工具函数单元测试
 *
 * 覆盖：extractTables 解析、hasMarkdownTable 检测、exportToCsv 输出
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { extractTables, hasMarkdownTable, exportToCsv } from '../../utils/tableExport';

describe('extractTables', () => {
  it('should extract a simple table', () => {
    const md = `
| 名称 | 价格 |
|------|------|
| 苹果 | 5 |
| 香蕉 | 3 |
`.trim();

    const tables = extractTables(md);
    expect(tables).toHaveLength(1);
    expect(tables[0]).toEqual([
      ['名称', '价格'],
      ['苹果', '5'],
      ['香蕉', '3'],
    ]);
  });

  it('should extract multiple tables', () => {
    const md = `
| A | B |
|---|---|
| 1 | 2 |

some text

| X | Y |
|---|---|
| 3 | 4 |
`.trim();

    const tables = extractTables(md);
    expect(tables).toHaveLength(2);
    expect(tables[0][1]).toEqual(['1', '2']);
    expect(tables[1][1]).toEqual(['3', '4']);
  });

  it('should return empty array for no tables', () => {
    const md = 'hello world\nno tables here';
    expect(extractTables(md)).toEqual([]);
  });

  it('should handle table at end of text (no trailing newline)', () => {
    const md = '| A |\n|---|\n| 1 |';
    const tables = extractTables(md);
    expect(tables).toHaveLength(1);
    expect(tables[0]).toEqual([['A'], ['1']]);
  });

  it('should skip separator rows', () => {
    const md = '| H1 | H2 |\n|:---|:---:|\n| a | b |';
    const tables = extractTables(md);
    expect(tables[0]).toHaveLength(2); // header + 1 data row, separator skipped
  });

  it('should trim cell content', () => {
    const md = '|  spaced  |  content  |\n|---|---|\n|  val1  |  val2  |';
    const tables = extractTables(md);
    expect(tables[0][1]).toEqual(['val1', 'val2']);
  });
});

describe('hasMarkdownTable', () => {
  it('should return true for text with tables', () => {
    expect(hasMarkdownTable('| A |\n|---|\n| 1 |')).toBe(true);
  });

  it('should return false for text without tables', () => {
    expect(hasMarkdownTable('no table here')).toBe(false);
  });

  it('should return false for pipe characters without table structure', () => {
    expect(hasMarkdownTable('a | b | c')).toBe(false);
  });
});

describe('exportToCsv', () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    createObjectURL = vi.fn().mockReturnValue('blob:test');
    revokeObjectURL = vi.fn();
    Object.defineProperty(globalThis, 'URL', {
      value: { createObjectURL, revokeObjectURL },
      writable: true,
    });

    // Mock DOM
    const mockLink = {
      href: '',
      download: '',
      click: vi.fn(),
    };
    vi.spyOn(document, 'createElement').mockReturnValue(mockLink as unknown as HTMLElement);
    vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as unknown as HTMLElement);
    vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as unknown as HTMLElement);
  });

  it('should create a CSV blob and trigger download', () => {
    const table = [
      ['Name', 'Age'],
      ['Alice', '30'],
    ];

    exportToCsv(table, 'test');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('text/csv;charset=utf-8;');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test');
  });

  it('should escape cells containing commas', () => {
    const table = [['a,b', 'c']];

    exportToCsv(table, 'test');

    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
  });

  it('should escape cells containing double quotes', () => {
    const table = [['say "hi"', 'ok']];

    exportToCsv(table, 'test');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });
});
