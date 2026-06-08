/**
 * plotly.js-dist-min 类型声明
 *
 * plotly.js-dist-min 是预编译的 plotly bundle, 没有官方 @types 包。
 * 用最小 any 声明让 TypeScript build 通过, 运行时行为不变。
 *
 * 详见 docs/document/TECH_沙盒产物协议.md (Phase 2e)
 */
declare module 'plotly.js-dist-min' {
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
