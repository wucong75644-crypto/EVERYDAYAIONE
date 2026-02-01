/**
 * 统一日志工具
 *
 * 提供格式化的日志输出，支持业务上下文。
 *
 * @example
 * import { logger } from '@/utils/logger';
 *
 * // 错误日志（带上下文）
 * logger.error('message:send', '发送消息失败', error, { conversationId, messageId });
 *
 * // 警告日志
 * logger.warn('polling', '轮询失败，将重试', { taskId, attempt: 2 });
 *
 * // 调试日志（仅开发环境）
 * logger.debug('api', '请求完成', { duration: 123 });
 */

type LogContext = Record<string, unknown>;

/**
 * 提取错误信息
 */
function extractErrorInfo(error: unknown): { message: string; stack?: string } {
  if (error instanceof Error) {
    return { message: error.message, stack: error.stack };
  }
  if (typeof error === 'string') {
    return { message: error };
  }
  return { message: String(error) };
}

/**
 * 格式化时间戳 HH:mm:ss
 */
function formatTime(): string {
  const now = new Date();
  return now.toTimeString().slice(0, 8);
}

/**
 * 格式化上下文为可读字符串
 */
function formatContext(context?: LogContext): string {
  if (!context || Object.keys(context).length === 0) {
    return '';
  }
  const entries = Object.entries(context)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => {
      if (typeof v === 'string' && v.length > 50) {
        return `${k}:"${v.slice(0, 47)}..."`;
      }
      if (typeof v === 'object') {
        return `${k}:${JSON.stringify(v)}`;
      }
      return `${k}:${v}`;
    });
  return entries.length > 0 ? ` | ${entries.join(', ')}` : '';
}

const isDev = import.meta.env.DEV;

/**
 * 统一日志工具
 */
export const logger = {
  /**
   * 错误日志
   *
   * @param scope - 模块/操作范围，如 'message:send', 'task:poll'
   * @param message - 错误描述
   * @param error - 原始错误对象
   * @param context - 业务上下文（conversationId、taskId 等）
   */
  error(
    scope: string,
    message: string,
    error?: unknown,
    context?: LogContext
  ): void {
    const time = formatTime();
    const errorInfo = error ? extractErrorInfo(error) : null;
    const contextStr = formatContext(context);
    const errorMsg = errorInfo ? `: ${errorInfo.message}` : '';

    console.error(`[ERROR] [${time}] [${scope}] ${message}${errorMsg}${contextStr}`);

    // 开发环境输出堆栈
    if (isDev && errorInfo?.stack) {
      console.error(errorInfo.stack);
    }
  },

  /**
   * 警告日志（用于可恢复的问题）
   *
   * @param scope - 模块/操作范围
   * @param message - 警告描述
   * @param context - 业务上下文
   */
  warn(scope: string, message: string, context?: LogContext): void {
    const time = formatTime();
    const contextStr = formatContext(context);

    console.warn(`[WARN] [${time}] [${scope}] ${message}${contextStr}`);
  },

  /**
   * 调试日志（仅开发环境）
   *
   * @param scope - 模块/操作范围
   * @param message - 调试信息
   * @param data - 附加数据
   */
  debug(scope: string, message: string, data?: unknown): void {
    if (!isDev) return;

    const time = formatTime();
    if (data !== undefined) {
      console.log(`[DEBUG] [${time}] [${scope}] ${message}`, data);
    } else {
      console.log(`[DEBUG] [${time}] [${scope}] ${message}`);
    }
  },

  /**
   * 信息日志（重要的业务事件）
   *
   * @param scope - 模块/操作范围
   * @param message - 信息描述
   * @param context - 业务上下文
   */
  info(scope: string, message: string, context?: LogContext): void {
    const time = formatTime();
    const contextStr = formatContext(context);

    console.info(`[INFO] [${time}] [${scope}] ${message}${contextStr}`);
  },
};

export default logger;
