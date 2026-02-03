/**
 * 性能监控工具
 *
 * 用于记录和分析页面加载性能指标，包括：
 * - TTI（Time to Interactive）：从导航到可交互的时间
 * - FCP（First Contentful Paint）：首次内容绘制
 * - 自定义标记点
 */

interface PerformanceMeasure {
  name: string;
  duration: number;
  timestamp: number;
}

class PerformanceMonitor {
  private marks = new Map<string, number>();
  private measures: PerformanceMeasure[] = [];
  private isEnabled = import.meta.env.DEV; // 仅开发环境启用日志

  /**
   * 标记一个时间点
   */
  mark(name: string): void {
    this.marks.set(name, performance.now());
  }

  /**
   * 测量从标记点到当前的时间差
   */
  measure(name: string, startMark: string): number | null {
    const start = this.marks.get(startMark);
    if (!start) {
      if (this.isEnabled) {
        console.warn(`[Perf] Mark "${startMark}" not found`);
      }
      return null;
    }

    const duration = performance.now() - start;
    const measure: PerformanceMeasure = {
      name,
      duration,
      timestamp: Date.now(),
    };

    this.measures.push(measure);

    if (this.isEnabled) {
      console.log(`[Perf] ${name}: ${duration.toFixed(2)}ms`);
    }

    return duration;
  }

  /**
   * 测量两个标记点之间的时间差
   */
  measureBetween(name: string, startMark: string, endMark: string): number | null {
    const start = this.marks.get(startMark);
    const end = this.marks.get(endMark);

    if (!start || !end) {
      return null;
    }

    const duration = end - start;
    this.measures.push({
      name,
      duration,
      timestamp: Date.now(),
    });

    if (this.isEnabled) {
      console.log(`[Perf] ${name}: ${duration.toFixed(2)}ms`);
    }

    return duration;
  }

  /**
   * 获取所有测量结果
   */
  getMeasures(): PerformanceMeasure[] {
    return [...this.measures];
  }

  /**
   * 清除所有标记和测量
   */
  clear(): void {
    this.marks.clear();
    this.measures = [];
  }

  /**
   * 获取 Web Vitals 指标（如果可用）
   */
  getWebVitals(): Record<string, number> {
    const vitals: Record<string, number> = {};

    // 获取 FCP
    const paintEntries = performance.getEntriesByType('paint');
    const fcp = paintEntries.find((entry) => entry.name === 'first-contentful-paint');
    if (fcp) {
      vitals.FCP = fcp.startTime;
    }

    // 获取导航时间
    const navEntries = performance.getEntriesByType('navigation');
    if (navEntries.length > 0) {
      const nav = navEntries[0] as PerformanceNavigationTiming;
      vitals.DNS = nav.domainLookupEnd - nav.domainLookupStart;
      vitals.TCP = nav.connectEnd - nav.connectStart;
      vitals.TTFB = nav.responseStart - nav.requestStart;
      vitals.DOMContentLoaded = nav.domContentLoadedEventEnd - nav.startTime;
      vitals.Load = nav.loadEventEnd - nav.startTime;
    }

    return vitals;
  }

  /**
   * 打印性能报告（开发环境）
   */
  report(): void {
    if (!this.isEnabled) return;

    console.group('[Perf] Performance Report');

    // 自定义测量
    if (this.measures.length > 0) {
      console.log('Custom Measures:');
      this.measures.forEach((m) => {
        console.log(`  ${m.name}: ${m.duration.toFixed(2)}ms`);
      });
    }

    // Web Vitals
    const vitals = this.getWebVitals();
    if (Object.keys(vitals).length > 0) {
      console.log('Web Vitals:');
      Object.entries(vitals).forEach(([key, value]) => {
        console.log(`  ${key}: ${value.toFixed(2)}ms`);
      });
    }

    console.groupEnd();
  }
}

// 导出单例
export const performanceMonitor = new PerformanceMonitor();
