/**
 * mediaHandlerUtils 单元测试
 * 测试媒体处理工具函数
 */

import { describe, it, expect, vi } from 'vitest';
import {
  extractErrorMessage,
  extractImageUrl,
  extractVideoUrl,
  handleGenerationError,
} from '../mediaHandlerUtils';
import * as messageService from '../../../services/message';
import { type Message } from '../../../services/message';
import { AxiosError } from 'axios';

// Mock message service
vi.mock('../../../services/message');

describe('mediaHandlerUtils', () => {
  describe('extractErrorMessage', () => {
    it('应该从 Axios 错误中提取后端消息', () => {
      const axiosError = {
        isAxiosError: true,
        response: {
          data: {
            error: {
              message: 'Backend error message',
            },
          },
        },
        message: 'Network error',
      } as unknown as AxiosError;

      const result = extractErrorMessage(axiosError);
      expect(result).toBe('Backend error message');
    });

    it('应该从 Error 对象中提取消息', () => {
      const error = new Error('Test error');
      const result = extractErrorMessage(error);
      expect(result).toBe('Test error');
    });

    it('应该处理未知错误类型', () => {
      const result = extractErrorMessage('string error');
      expect(result).toBe('未知错误');
    });

    it('应该处理空后端响应', () => {
      const axiosError = {
        isAxiosError: true,
        response: {
          data: {},
        },
        message: 'Network error',
      } as unknown as AxiosError;

      const result = extractErrorMessage(axiosError);
      expect(result).toBe('Network error');
    });
  });

  describe('extractImageUrl', () => {
    it('应该成功提取图片 URL', () => {
      const result = {
        image_urls: ['https://example.com/image.jpg'],
      };

      const url = extractImageUrl(result);
      expect(url).toBe('https://example.com/image.jpg');
    });

    it('应该处理空数组', () => {
      const result = {
        image_urls: [],
      };

      const url = extractImageUrl(result);
      expect(url).toBeUndefined();
    });

    it('应该处理无效类型', () => {
      const result = {
        image_urls: 'not-an-array',
      };

      const url = extractImageUrl(result);
      expect(url).toBeUndefined();
    });

    it('应该处理 null/undefined', () => {
      expect(extractImageUrl(null)).toBeUndefined();
      expect(extractImageUrl(undefined)).toBeUndefined();
      expect(extractImageUrl({})).toBeUndefined();
    });
  });

  describe('extractVideoUrl', () => {
    it('应该成功提取视频 URL', () => {
      const result = {
        video_url: 'https://example.com/video.mp4',
      };

      const url = extractVideoUrl(result);
      expect(url).toBe('https://example.com/video.mp4');
    });

    it('应该处理无效类型', () => {
      const result = {
        video_url: 123,
      };

      const url = extractVideoUrl(result);
      expect(url).toBeUndefined();
    });

    it('应该处理 null/undefined', () => {
      expect(extractVideoUrl(null)).toBeUndefined();
      expect(extractVideoUrl(undefined)).toBeUndefined();
      expect(extractVideoUrl({})).toBeUndefined();
    });
  });

  describe('handleGenerationError', () => {
    it('应该创建错误消息', async () => {
      const mockErrorMessage = {
        id: 'error-1',
        conversation_id: 'conv-1',
        role: 'assistant',
        content: '图片生成失败: Test error',
        is_error: true,
      };

      vi.mocked(messageService.createMessage).mockResolvedValue(mockErrorMessage as Message);

      const result = await handleGenerationError(
        'conv-1',
        '图片生成失败',
        new Error('Test error'),
        new Date().toISOString()
      );

      expect(result).toEqual(mockErrorMessage);
      expect(messageService.createMessage).toHaveBeenCalledWith(
        'conv-1',
        expect.objectContaining({
          content: expect.stringContaining('Test error'),
          is_error: true,
        })
      );
    });

    it('应该处理创建消息失败的情况', async () => {
      vi.mocked(messageService.createMessage).mockRejectedValue(
        new Error('Database error')
      );

      const result = await handleGenerationError(
        'conv-1',
        '图片生成失败',
        new Error('Test error')
      );

      expect(result).toMatchObject({
        conversation_id: 'conv-1',
        is_error: true,
      });
    });

    it('应该包含生成参数', async () => {
      const generationParams = {
        image: {
          aspectRatio: '1:1' as const,
          outputFormat: 'png' as const,
          model: 'test-model',
        },
      };

      vi.mocked(messageService.createMessage).mockResolvedValue({} as Message);

      await handleGenerationError(
        'conv-1',
        '图片生成失败',
        new Error('Test error'),
        undefined,
        generationParams
      );

      expect(messageService.createMessage).toHaveBeenCalledWith(
        'conv-1',
        expect.objectContaining({
          generation_params: generationParams,
        })
      );
    });
  });
});
