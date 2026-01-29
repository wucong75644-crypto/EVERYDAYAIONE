/**
 * 性能监控工具
 * 用于跟踪关键操作的性能指标
 */

interface PerformanceMetric {
  name: string;
  startTime: number;
  endTime?: number;
  duration?: number;
  metadata?: Record<string, unknown>;
}

class PerformanceMonitor {
  private metrics: Map<string, PerformanceMetric> = new Map();
  private enabled: boolean = import.meta.env.DEV || import.meta.env.VITE_ENABLE_PERF_MONITOR === 'true';

  /**
   * 开始性能测量
   */
  start(name: string, metadata?: Record<string, unknown>): void {
    if (!this.enabled) return;

    this.metrics.set(name, {
      name,
      startTime: performance.now(),
      metadata,
    });
  }

  /**
   * 结束性能测量并记录
   */
  end(name: string, additionalMetadata?: Record<string, unknown>): number | null {
    if (!this.enabled) return null;

    const metric = this.metrics.get(name);
    if (!metric) {
      console.warn(`Performance metric "${name}" not found`);
      return null;
    }

    const endTime = performance.now();
    const duration = endTime - metric.startTime;

    metric.endTime = endTime;
    metric.duration = duration;
    if (additionalMetadata) {
      metric.metadata = { ...metric.metadata, ...additionalMetadata };
    }

    this.logMetric(metric);
    this.metrics.delete(name);

    return duration;
  }

  /**
   * 测量异步操作
   */
  async measure<T>(
    name: string,
    fn: () => Promise<T>,
    metadata?: Record<string, unknown>
  ): Promise<T> {
    if (!this.enabled) return fn();

    this.start(name, metadata);
    try {
      const result = await fn();
      this.end(name, { success: true });
      return result;
    } catch (error) {
      this.end(name, { success: false, error: String(error) });
      throw error;
    }
  }

  /**
   * 测量同步操作
   */
  measureSync<T>(
    name: string,
    fn: () => T,
    metadata?: Record<string, unknown>
  ): T {
    if (!this.enabled) return fn();

    this.start(name, metadata);
    try {
      const result = fn();
      this.end(name, { success: true });
      return result;
    } catch (error) {
      this.end(name, { success: false, error: String(error) });
      throw error;
    }
  }

  /**
   * 记录性能指标
   */
  private logMetric(metric: PerformanceMetric): void {
    const { name, duration, metadata } = metric;

    // 格式化输出
    const metadataStr = metadata ? ` | ${JSON.stringify(metadata)}` : '';
    const durationStr = duration ? `${duration.toFixed(2)}ms` : 'N/A';

    // 根据耗时选择日志级别
    if (duration && duration > 3000) {
      console.warn(`⚠️ [Perf] ${name}: ${durationStr}${metadataStr}`);
    } else if (duration && duration > 1000) {
      console.log(`⏱️  [Perf] ${name}: ${durationStr}${metadataStr}`);
    } else {
      console.debug(`✅ [Perf] ${name}: ${durationStr}${metadataStr}`);
    }

    // 发送到监控服务（如果需要）
    this.sendToMonitoringService(metric);
  }

  /**
   * 发送到外部监控服务
   */
  private sendToMonitoringService(metric: PerformanceMetric): void {
    void metric;
    // 这里可以集成 Sentry、DataDog 等监控服务
    // 示例：
    // if (window.Sentry) {
    //   window.Sentry.captureMessage('Performance Metric', {
    //     level: 'info',
    //     extra: metric,
    //   });
    // }
  }

  /**
   * 获取页面性能指标
   */
  getPageMetrics(): Record<string, number> | null {
    if (!this.enabled || !performance.getEntriesByType) return null;

    const [navigation] = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[];
    if (!navigation) return null;

    return {
      // DNS 解析时间
      dns: navigation.domainLookupEnd - navigation.domainLookupStart,
      // TCP 连接时间
      tcp: navigation.connectEnd - navigation.connectStart,
      // 请求时间
      request: navigation.responseStart - navigation.requestStart,
      // 响应时间
      response: navigation.responseEnd - navigation.responseStart,
      // DOM 解析时间
      domParse: navigation.domContentLoadedEventEnd - navigation.responseEnd,
      // 资源加载时间
      resourceLoad: navigation.loadEventEnd - navigation.domContentLoadedEventEnd,
      // 总加载时间
      totalLoad: navigation.loadEventEnd - navigation.fetchStart,
      // 首次渲染时间
      firstPaint: this.getFirstPaint(),
      // 首次内容渲染时间
      firstContentfulPaint: this.getFirstContentfulPaint(),
    };
  }

  /**
   * 获取首次渲染时间
   */
  private getFirstPaint(): number {
    const entries = performance.getEntriesByName('first-paint');
    return entries.length > 0 ? entries[0].startTime : 0;
  }

  /**
   * 获取首次内容渲染时间
   */
  private getFirstContentfulPaint(): number {
    const entries = performance.getEntriesByName('first-contentful-paint');
    return entries.length > 0 ? entries[0].startTime : 0;
  }

  /**
   * 记录页面性能指标
   */
  logPageMetrics(): void {
    if (!this.enabled) return;

    // 等待页面加载完成
    if (document.readyState !== 'complete') {
      window.addEventListener('load', () => this.logPageMetrics());
      return;
    }

    setTimeout(() => {
      const metrics = this.getPageMetrics();
      if (metrics) {
        console.group('📊 Page Performance Metrics');
        console.table(metrics);
        console.groupEnd();
      }
    }, 0);
  }

  /**
   * 清除所有未完成的测量
   */
  clear(): void {
    this.metrics.clear();
  }
}

// 单例实例
export const performanceMonitor = new PerformanceMonitor();

// 关键操作埋点
export const PerfMarkers = {
  // 消息相关
  MESSAGE_SEND: 'message:send',
  MESSAGE_STREAM: 'message:stream',
  MESSAGE_LOAD: 'message:load',

  // 图片相关
  IMAGE_GENERATION: 'image:generation',
  IMAGE_UPLOAD: 'image:upload',
  IMAGE_POLLING: 'image:polling',

  // 视频相关
  VIDEO_GENERATION: 'video:generation',
  VIDEO_POLLING: 'video:polling',

  // UI 相关
  CONVERSATION_SWITCH: 'ui:conversation-switch',
  SCROLL_POSITION: 'ui:scroll-position',
  RENDER: 'ui:render',

  // API 相关
  API_REQUEST: 'api:request',
  API_RESPONSE: 'api:response',
} as const;

// 便捷函数
export function measureAsync<T>(
  name: string,
  fn: () => Promise<T>,
  metadata?: Record<string, unknown>
): Promise<T> {
  return performanceMonitor.measure(name, fn, metadata);
}

export function measureSync<T>(
  name: string,
  fn: () => T,
  metadata?: Record<string, unknown>
): T {
  return performanceMonitor.measureSync(name, fn, metadata);
}

// 页面加载时自动记录性能指标
if (typeof window !== 'undefined') {
  performanceMonitor.logPageMetrics();
}
