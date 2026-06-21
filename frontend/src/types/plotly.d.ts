/**
 * plotly.js-basic-dist-min 类型声明
 *
 * plotly.js-basic-dist-min 是预编译的 plotly basic bundle (~200KB gzip),
 * 比 plotly.js-dist-min 小 5 倍,覆盖 scatter/bar/pie/heatmap/contour/
 * histogram/box/candlestick 等基础图表,LLM 数据可视化 99% 场景适用。
 *
 * 用最小 any 声明让 TypeScript build 通过, 运行时行为不变。
 *
 * 详见 docs/document/TECH_沙盒产物协议.md (Phase 2e)
 */
declare module 'plotly.js-basic-dist-min' {
  // 只声明我们 PlotlyBlock.tsx 用到的接口
  export function newPlot(
    container: HTMLElement,
    data: unknown[],
    layout?: Record<string, unknown>,
    config?: Record<string, unknown>,
  ): Promise<unknown>;

  export function purge(container: HTMLElement): void;

  const _default: {
    newPlot: typeof newPlot;
    purge: typeof purge;
  };
  export default _default;
}
