/**
 * 消息处理器共享工具
 * 包含错误提取等工具函数
 */

import axios from 'axios';

/** 从错误中提取友好消息 */
export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const responseData = error.response?.data;
    const backendMessage =
      responseData?.error?.message || responseData?.message || responseData?.detail;
    return backendMessage || error.message;
  }
  return error instanceof Error ? error.message : '未知错误';
}
