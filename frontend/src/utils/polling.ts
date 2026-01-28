/**
 * 轮询工具模块
 *
 * 提供通用的轮询功能，支持：
 * - 可配置的轮询间隔
 * - 最大轮询时长限制
 * - 连续失败容错
 * - 原子性完成检测（防止竞态）
 */

/** 轮询回调 */
export interface PollingCallbacks {
  onSuccess: (result: unknown) => void;
  onError: (error: Error) => void;
  onProgress?: (progress: number) => void;
}

/** 轮询配置 */
export interface PollingConfig {
  intervalId: ReturnType<typeof setInterval>;
  pollFn: () => Promise<{ done: boolean; result?: unknown; error?: Error }>;
  callbacks: PollingCallbacks;
}

/** 轮询选项 */
export interface PollingOptions {
  /** 轮询间隔（毫秒），默认 2000 */
  interval?: number;
  /** 最大轮询时长（毫秒），超时后调用 onError */
  maxDuration?: number;
}

/** 轮询管理器 */
export class PollingManager {
  private configs = new Map<string, PollingConfig>();

  /**
   * 检查任务是否正在轮询
   */
  has(taskId: string): boolean {
    return this.configs.has(taskId);
  }

  /**
   * 获取轮询配置
   */
  get(taskId: string): PollingConfig | undefined {
    return this.configs.get(taskId);
  }

  /**
   * 开始轮询
   * @param taskId 任务ID
   * @param pollFn 轮询函数，返回 { done, result?, error? }
   * @param callbacks 回调函数
   * @param options 轮询选项
   * @returns 清理函数
   */
  start(
    taskId: string,
    pollFn: () => Promise<{ done: boolean; result?: unknown; error?: Error }>,
    callbacks: PollingCallbacks,
    options: PollingOptions = {}
  ): () => void {
    const { interval = 2000, maxDuration } = options;
    const startTime = Date.now();

    // 轮询执行函数
    const executePoll = async () => {
      // 检查是否超过最大轮询时长
      if (maxDuration) {
        const elapsed = Date.now() - startTime;
        if (elapsed > maxDuration) {
          if (!this.configs.has(taskId)) return;
          this.stop(taskId);
          const minutes = Math.round(maxDuration / 60000);
          callbacks.onError(new Error(`任务轮询超时，已等待 ${minutes} 分钟`));
          return;
        }
      }

      try {
        const result = await pollFn();

        if (result.done) {
          // 原子性检查：防止竞态时多个 executePoll 重复触发回调
          if (!this.configs.has(taskId)) return;
          this.stop(taskId);
          result.error ? callbacks.onError(result.error) : callbacks.onSuccess(result.result);
        }
        // 任务未完成（pending/running）：等待下次轮询间隔
      } catch (error) {
        // 请求超时/网络错误 ≠ 任务失败
        // 只有明确的 failed 状态才是真正的失败（通过 result.error 处理）
        console.warn(`轮询任务 ${taskId} 请求失败，将在下次间隔后重试:`, error);
        // 继续等待下次轮询，不调用 onError
      }
    };

    const intervalId = setInterval(executePoll, interval);

    // 先注册配置，再执行立即轮询
    this.configs.set(taskId, { intervalId, pollFn, callbacks });

    // 立即执行一次
    executePoll();

    // 返回清理函数
    return () => this.stop(taskId);
  }

  /**
   * 停止轮询
   */
  stop(taskId: string): void {
    const config = this.configs.get(taskId);
    if (config) {
      clearInterval(config.intervalId);
      this.configs.delete(taskId);
    }
  }

  /**
   * 停止所有轮询
   */
  stopAll(): void {
    for (const [taskId] of this.configs) {
      this.stop(taskId);
    }
  }

  /**
   * 获取当前轮询任务数量
   */
  get size(): number {
    return this.configs.size;
  }
}

/** 默认轮询管理器实例 */
export const pollingManager = new PollingManager();
