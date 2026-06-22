/**
 * 预览注册表测试 — 验证扩展名/mime 到 adapter 的路由
 */
import { describe, it, expect } from 'vitest';
import { resolveAdapter, canPreview } from '../registry';
import type { PreviewItem } from '../types';

function item(filename: string, mimeType?: string | null): PreviewItem {
  return { filename, mimeType };
}

describe('resolveAdapter — 扩展名路由', () => {
  it.each(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'heic'])(
    '.%s → image',
    (ext) => {
      expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('image');
    },
  );

  it.each(['mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v'])('.%s → video', (ext) => {
    expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('video');
  });

  it('pdf → pdf adapter', () => {
    expect(resolveAdapter(item('a.pdf'))?.id).toBe('pdf');
  });

  it.each(['xlsx', 'xls', 'csv', 'tsv'])('.%s → spreadsheet', (ext) => {
    expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('spreadsheet');
  });

  it.each(['txt', 'md', 'log', 'json', 'yaml', 'yml', 'xml', 'py', 'js', 'ts', 'html', 'css', 'sql'])(
    '.%s → text',
    (ext) => {
      expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('text');
    },
  );

  it('.docx → docx adapter (mammoth.js 前端)', () => {
    expect(resolveAdapter(item('a.docx'))?.id).toBe('docx');
  });

  it.each(['doc', 'pptx', 'ppt'])('.%s → pptx adapter (后端 LibreOffice 转 PDF)', (ext) => {
    expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('pptx');
  });

  it.each(['zip', 'rar', '7z', 'mystery', 'exe'])('.%s → fallback', (ext) => {
    expect(resolveAdapter(item(`a.${ext}`))?.id).toBe('fallback');
  });
});

describe('resolveAdapter — mime 兜底', () => {
  it('无扩展名但 mime image/jpeg → image', () => {
    expect(resolveAdapter(item('noext', 'image/jpeg'))?.id).toBe('image');
  });
  it('无扩展名但 mime video/mp4 → video', () => {
    expect(resolveAdapter(item('movie', 'video/mp4'))?.id).toBe('video');
  });
});

describe('resolveAdapter — 优先级', () => {
  it('扩展名图片 + mime video → image 优先（先 match image=100）', () => {
    expect(resolveAdapter(item('a.png', 'video/mp4'))?.id).toBe('image');
  });
});

describe('canPreview', () => {
  it('支持的类型返回 true', () => {
    expect(canPreview(item('a.png'))).toBe(true);
    expect(canPreview(item('a.mp4'))).toBe(true);
    expect(canPreview(item('a.pdf'))).toBe(true);
    expect(canPreview(item('a.xlsx'))).toBe(true);
    expect(canPreview(item('a.txt'))).toBe(true);
  });
  it('fallback 类型返回 false', () => {
    expect(canPreview(item('a.zip'))).toBe(false);
    expect(canPreview(item('a.exe'))).toBe(false);
  });
});

describe('大写扩展名', () => {
  it('PNG 大写仍命中 image', () => {
    expect(resolveAdapter(item('IMG.PNG'))?.id).toBe('image');
  });
});
