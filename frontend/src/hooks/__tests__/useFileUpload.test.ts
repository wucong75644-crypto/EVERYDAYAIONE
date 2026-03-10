/**
 * useFileUpload Hook 单测
 *
 * 覆盖 PDF 文件上传相关功能：
 * - 初始状态
 * - 文件校验（类型、大小）
 * - 文件删除
 * - 状态派生（hasFiles、isUploading）
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFileUpload } from '../useFileUpload';

// Mock fileUpload service
vi.mock('../../services/fileUpload', () => ({
  uploadFile: vi.fn(),
}));

// Mock logger
vi.mock('../../utils/logger', () => ({
  logger: { error: vi.fn(), info: vi.fn(), debug: vi.fn(), warn: vi.fn() },
}));

describe('useFileUpload', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should initialize with empty state', () => {
    const { result } = renderHook(() => useFileUpload());

    expect(result.current.files).toEqual([]);
    expect(result.current.uploadedFileUrls).toEqual([]);
    expect(result.current.isUploading).toBe(false);
    expect(result.current.uploadError).toBeNull();
    expect(result.current.hasFiles).toBe(false);
  });

  it('should set upload error for invalid file type', async () => {
    const { result } = renderHook(() => useFileUpload());

    const invalidFile = new File(['content'], 'test.txt', { type: 'text/plain' });
    const fakeEvent = {
      target: { files: [invalidFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.uploadError).toBe('仅支持 PDF 格式的文档');
    expect(result.current.files).toEqual([]);
  });

  it('should set upload error for oversized file', async () => {
    const { result } = renderHook(() => useFileUpload());

    // Create a "large" file mock (can't actually create 51MB in test)
    const bigFile = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(bigFile, 'size', { value: 51 * 1024 * 1024 });

    const fakeEvent = {
      target: { files: [bigFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.uploadError).toMatch(/文件大小不能超过/);
  });

  it('should add file and upload successfully', async () => {
    const { uploadFile } = await import('../../services/fileUpload');
    (uploadFile as ReturnType<typeof vi.fn>).mockResolvedValue({
      url: 'https://cdn.example.com/report.pdf',
      name: 'report.pdf',
      mime_type: 'application/pdf',
      size: 1000,
    });

    const { result } = renderHook(() => useFileUpload());

    const pdfFile = new File(['%PDF'], 'report.pdf', { type: 'application/pdf' });
    const fakeEvent = {
      target: { files: [pdfFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.files).toHaveLength(1);
    expect(result.current.files[0].url).toBe('https://cdn.example.com/report.pdf');
    expect(result.current.files[0].isUploading).toBe(false);
    expect(result.current.hasFiles).toBe(true);
    expect(result.current.uploadedFileUrls).toHaveLength(1);
    expect(result.current.uploadedFileUrls[0].url).toBe('https://cdn.example.com/report.pdf');
  });

  it('should handle upload failure gracefully', async () => {
    const { uploadFile } = await import('../../services/fileUpload');
    (uploadFile as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useFileUpload());

    const pdfFile = new File(['%PDF'], 'fail.pdf', { type: 'application/pdf' });
    const fakeEvent = {
      target: { files: [pdfFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.files).toHaveLength(1);
    expect(result.current.files[0].error).toBe('上传失败');
    expect(result.current.files[0].isUploading).toBe(false);
  });

  it('should remove file by id', async () => {
    const { uploadFile } = await import('../../services/fileUpload');
    (uploadFile as ReturnType<typeof vi.fn>).mockResolvedValue({
      url: 'https://cdn.example.com/doc.pdf',
      name: 'doc.pdf',
      mime_type: 'application/pdf',
      size: 500,
    });

    const { result } = renderHook(() => useFileUpload());

    const pdfFile = new File(['%PDF'], 'doc.pdf', { type: 'application/pdf' });
    const fakeEvent = {
      target: { files: [pdfFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.files).toHaveLength(1);
    const fileId = result.current.files[0].id;

    act(() => {
      result.current.handleRemoveFile(fileId);
    });

    expect(result.current.files).toEqual([]);
    expect(result.current.hasFiles).toBe(false);
  });

  it('should remove all files', async () => {
    const { uploadFile } = await import('../../services/fileUpload');
    (uploadFile as ReturnType<typeof vi.fn>).mockResolvedValue({
      url: 'https://cdn.example.com/doc.pdf',
      name: 'doc.pdf',
      mime_type: 'application/pdf',
      size: 500,
    });

    const { result } = renderHook(() => useFileUpload());

    const pdfFile = new File(['%PDF'], 'doc.pdf', { type: 'application/pdf' });
    const fakeEvent = {
      target: { files: [pdfFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    act(() => {
      result.current.handleRemoveAllFiles();
    });

    expect(result.current.files).toEqual([]);
    expect(result.current.uploadError).toBeNull();
  });

  it('should clear upload error', async () => {
    const { result } = renderHook(() => useFileUpload());

    // Trigger an error first
    const invalidFile = new File(['content'], 'test.txt', { type: 'text/plain' });
    const fakeEvent = {
      target: { files: [invalidFile], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.uploadError).not.toBeNull();

    act(() => {
      result.current.clearUploadError();
    });

    expect(result.current.uploadError).toBeNull();
  });

  it('should handle empty file input', async () => {
    const { result } = renderHook(() => useFileUpload());

    const fakeEvent = {
      target: { files: null, value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    await act(async () => {
      await result.current.handleFileSelect(fakeEvent);
    });

    expect(result.current.files).toEqual([]);
  });

  it('should respect custom maxSizeMB', async () => {
    const { result } = renderHook(() => useFileUpload());

    const file = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(file, 'size', { value: 11 * 1024 * 1024 }); // 11MB

    const fakeEvent = {
      target: { files: [file], value: '' },
    } as unknown as React.ChangeEvent<HTMLInputElement>;

    // maxSizeMB = 10, should reject 11MB file
    await act(async () => {
      await result.current.handleFileSelect(fakeEvent, 10);
    });

    expect(result.current.uploadError).toMatch(/文件大小不能超过 10MB/);
  });
});
