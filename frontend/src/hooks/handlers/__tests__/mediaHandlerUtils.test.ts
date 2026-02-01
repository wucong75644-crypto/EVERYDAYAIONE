/**
 * mediaHandlerUtils 单元测试
 * 测试媒体处理工具函数
 */

import { describe, it, expect } from 'vitest';
import { extractErrorMessage } from '../mediaHandlerUtils';
import { AxiosError } from 'axios';

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
});
