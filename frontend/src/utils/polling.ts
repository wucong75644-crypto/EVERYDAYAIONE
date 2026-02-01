/**
 * 轮询类型定义
 *
 * 仅提供类型定义，实际轮询逻辑在 useTaskStore 中实现
 * useTaskStore 的实现更完善：支持连续失败计数、锁续约、taskCoordinator 集成
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
  lockRenewalId?: ReturnType<typeof setInterval>;
}
