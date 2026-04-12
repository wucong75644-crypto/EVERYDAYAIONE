/**
 * fileUtils 工具函数单元测试
 *
 * 覆盖：getFileIcon、formatFileSize
 */

import { describe, it, expect } from 'vitest';
import { getFileIcon, getFileIconColor, formatFileSize } from '../fileUtils';

describe('getFileIcon', () => {
  it('should return chart icon for xlsx', () => {
    expect(getFileIcon('report.xlsx')).toBe('\uD83D\uDCCA');
  });

  it('should return chart icon for csv', () => {
    expect(getFileIcon('data.csv')).toBe('\uD83D\uDCCA');
  });

  it('should return document icon for pdf', () => {
    expect(getFileIcon('doc.pdf')).toBe('\uD83D\uDCC4');
  });

  it('should return text icon for txt', () => {
    expect(getFileIcon('readme.txt')).toBe('\uD83D\uDCC3');
  });

  it('should return package icon for zip', () => {
    expect(getFileIcon('archive.zip')).toBe('\uD83D\uDCE6');
  });

  it('should return paperclip for unknown extension', () => {
    expect(getFileIcon('something.abc')).toBe('\uD83D\uDCCE');
  });

  it('should handle no extension', () => {
    expect(getFileIcon('noext')).toBe('\uD83D\uDCCE');
  });
});

describe('getFileIconColor', () => {
  it('should return red for pdf', () => {
    expect(getFileIconColor('doc.pdf')).toContain('red');
  });

  it('should return green for csv', () => {
    expect(getFileIconColor('data.csv')).toContain('green');
  });

  it('should return green for xlsx', () => {
    expect(getFileIconColor('report.xlsx')).toContain('green');
  });

  it('should return blue for docx', () => {
    expect(getFileIconColor('file.docx')).toContain('blue');
  });

  it('should return orange for pptx', () => {
    expect(getFileIconColor('slides.pptx')).toContain('orange');
  });

  it('should return purple for code files', () => {
    expect(getFileIconColor('app.py')).toContain('purple');
    expect(getFileIconColor('index.ts')).toContain('purple');
  });

  it('should return yellow for zip', () => {
    expect(getFileIconColor('archive.zip')).toContain('yellow');
  });

  it('should return secondary color for unknown', () => {
    expect(getFileIconColor('readme.txt')).toContain('--s-text-secondary');
  });
});

describe('formatFileSize', () => {
  it('should return empty string for undefined', () => {
    expect(formatFileSize(undefined)).toBe('');
  });

  it('should return empty string for 0', () => {
    expect(formatFileSize(0)).toBe('');
  });

  it('should format bytes', () => {
    expect(formatFileSize(500)).toBe('500B');
  });

  it('should format kilobytes', () => {
    expect(formatFileSize(2048)).toBe('2.0KB');
  });

  it('should format megabytes', () => {
    expect(formatFileSize(1048576)).toBe('1.0MB');
  });

  it('should format large file', () => {
    expect(formatFileSize(5242880)).toBe('5.0MB');
  });
});
