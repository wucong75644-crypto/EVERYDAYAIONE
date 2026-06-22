/**
 * Plotly 图表渲染块(对话场景 / 大厂标准)
 *
 * 由 ChartBlock 在 spec_format='plotly' 时分发,
 * 走 plotly.js-basic-dist-min (按需加载,~200KB gzip,比 full dist 小 5 倍)。
 *
 * basic 版支持: scatter / bar / pie / heatmap / contour / histogram / box /
 *                candlestick / scatterpolar 等基础图表,覆盖 LLM 99% 场景。
 * 不支持: 3D / 地图(LLM 极少用)。
 *
 * ============================================================
 * 尺寸 / 视觉策略(对齐 Databricks / Hex / Streamlit / Jupyter)
 * ============================================================
 *
 * 宽度: 100% 跟随容器(响应窗口/侧边栏 resize)
 * 高度: 固定 CHART_HEIGHT_PX (对话场景不让图表吞噬上下文流)
 * 兜底: 容器 overflow:hidden(plotly 任何超出 450 都被截,绝不覆盖下方文字)
 *
 * 防御性:剥离 LLM 给的 width / height / autosize / margin,
 *        所有尺寸由我们控制,LLM 仍可改 title / xaxis 内容 / 配色等。
 *
 * 视觉: PROFESSIONAL_TEMPLATE (中文字体 + 商业感配色 + 浅灰网格 +
 *       legend 水平放底部 + 标题左对齐)。
 *
 * 工具栏: TOOLBAR_CONFIG (移除 plotly logo / cloud 跳转 / 冗余按钮)。
 *
 * 详见 docs/document/TECH_沙盒产物协议.md
 */
import { useEffect, useRef, useState, memo } from 'react';
import { logger } from '../../../utils/logger';

// ============================================================
// 常量
// ============================================================

/** 图表固定高度(px) — 对话场景标准,避免巨型图表吞噬对话流 */
const CHART_HEIGHT_PX = 450;

/** 我们完全控制的 layout 字段集 — 剥离 LLM 在这些字段上的输入,
 *  避免 LLM 设 height:800 / margin:{t:200} 等让图表撑爆容器。 */
const STRIPPED_LAYOUT_FIELDS = ['width', 'height', 'autosize', 'margin'] as const;

/** 视觉模板:对齐 plotly 官网 demo 风格 + 中文场景优化 */
const PROFESSIONAL_TEMPLATE = {
  layout: {
    font: {
      family: '"PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      size: 13,
      color: '#1f2937',
    },
    paper_bgcolor: 'white',
    plot_bgcolor: 'white',
    // 商业感配色(蓝/绿/橙/红/紫/粉/青/草绿)
    colorway: [
      '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
      '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16',
    ],
    // 我们控制 margin (LLM 给的会被剥离),legend 在顶部所以 t 大一点(标题 + legend 两行)
    margin: { l: 60, r: 30, t: 90, b: 50, pad: 4 },
    xaxis: {
      gridcolor: '#f3f4f6',
      linecolor: '#e5e7eb',
      zerolinecolor: '#e5e7eb',
      tickfont: { size: 12, color: '#4b5563' },
      automargin: true,
      showline: true,
    },
    yaxis: {
      gridcolor: '#f3f4f6',
      linecolor: '#e5e7eb',
      zerolinecolor: '#e5e7eb',
      tickfont: { size: 12, color: '#4b5563' },
      automargin: true,
      showline: true,
    },
    title: {
      font: { size: 16, color: '#111827', weight: 600 },
      x: 0.05,
      xanchor: 'left',
      y: 0.95,
    },
    hoverlabel: {
      font: { family: '"PingFang SC", "Microsoft YaHei", sans-serif', size: 12 },
      bgcolor: 'white',
      bordercolor: '#e5e7eb',
    },
    // legend 水平放顶部居中(标题左对齐 + legend 一行在标题下方):
    // 默认 plotly express 把 legend 放右侧垂直会挤压绘图区宽度,
    // 实测改顶部水平后绘图区宽度铺满,且不占底部 X 轴/X 轴标题空间。
    legend: {
      orientation: 'h',
      x: 0.5,
      y: 1.08,
      xanchor: 'center',
      yanchor: 'bottom',
      font: { size: 12, color: '#4b5563' },
      bgcolor: 'rgba(255,255,255,0)',
      title: { text: '' },  // 隐藏 plotly express 自动加的 'variable' 等技术标题
    },
    bargap: 0.3,
  },
};

/** 工具栏配置:精简实用,移除 plotly logo 和 cloud 跳转 */
const TOOLBAR_CONFIG: Record<string, unknown> = {
  // 移除右下角 "Made with Plotly" 跳转角标
  displaylogo: false,
  // 鼠标 hover 才显示工具栏(默认常驻太占视觉)
  displayModeBar: 'hover',
  // 删除冗余 / 高级按钮(只保留常用:保存/缩放/平移/重置/全屏)
  modeBarButtonsToRemove: [
    'lasso2d',                // 套索选择
    'select2d',               // 矩形选区
    'autoScale2d',            // 自适应缩放(跟重置重复)
    'hoverClosestCartesian',  // 悬浮模式切换
    'hoverCompareCartesian',  // 悬浮对比模式
    'toggleSpikelines',       // 十字辅助线
    'sendDataToCloud',        // 发送到 plotly cloud (跳转外站)
    'editInChartStudio',      // 在 plotly cloud 编辑 (跳转外站)
  ],
  // 保存图片:中文文件名 + 高清 + 默认尺寸
  toImageButtonOptions: {
    filename: '图表',
    format: 'png',
    height: 600,
    width: 1000,
    scale: 2,
  },
  responsive: true,
  locale: 'zh-CN',
};

// ============================================================
// 工具函数
// ============================================================

let plotlyPromise: Promise<typeof import('plotly.js-basic-dist-min')> | null = null;
function getPlotly() {
  if (!plotlyPromise) {
    plotlyPromise = import('plotly.js-basic-dist-min');
  }
  return plotlyPromise;
}

/** 剥离 LLM 给的尺寸字段(width / height / autosize / margin)
 *  让我们完全控制图表尺寸,LLM 仍可改 title / xaxis / 配色等。 */
export function stripSizeFields(
  layout: Record<string, unknown>,
): Record<string, unknown> {
  const cleaned: Record<string, unknown> = { ...layout };
  for (const f of STRIPPED_LAYOUT_FIELDS) {
    delete cleaned[f];
  }
  return cleaned;
}

// ============================================================
// 子组件
// ============================================================

function LoadingOverlay() {
  return (
    <div className="absolute inset-0 rounded-xl flex items-center justify-center bg-hover">
      <svg className="w-8 h-8 text-text-disabled animate-pulse" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M3 3v18h18" /><path d="M7 16l4-8 4 4 4-6" />
      </svg>
    </div>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="my-3 rounded-xl border border-border-default bg-surface-card p-4">
      <div className="text-sm font-medium text-error mb-2">Plotly 图表渲染失败</div>
      <p className="text-xs text-text-tertiary">{message}</p>
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================

interface PlotlyBlockProps {
  option: Record<string, unknown>;
  /** title 已不用 — plotly 内部 layout.title 由 template 渲染,
   *  外层重复渲染会出现双标题 + 空白(对齐 ChartBlock 处理)。
   *  保留 prop 仅供未来 fullscreen 扩展。 */
  title?: string;
}

type RenderState = 'loading' | 'ready' | 'error';

function PlotlyBlockInner({ option }: PlotlyBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<RenderState>('loading');
  const [errMsg, setErrMsg] = useState('');

  // 主渲染:option 变化时重新 newPlot
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;

    (async () => {
      try {
        const Plotly = await getPlotly();
        if (disposed || !containerRef.current) return;

        // 1. 准备 data: LLM 直接给的 data 列表
        const data = (option.data as unknown[]) || [];

        // 2. 准备 layout:
        //    - template 提供视觉默认
        //    - LLM 的 layout 覆盖 template (但 width/height/autosize/margin 被剥离)
        //    - 强制 autosize=true 让 plotly 用容器尺寸
        //    - 强制 legend 顶部水平居中 + 隐藏自动标题
        //      (plotly express 在 fig.layout.legend 设默认值会覆盖 template.layout.legend,
        //       不强制就出现每次 legend 位置/标题不固定,如 px.line(y=[a,b]) 自动加 'variable')
        const llmLayout = (option.layout as Record<string, unknown>) || {};
        const layout = {
          template: PROFESSIONAL_TEMPLATE,
          ...stripSizeFields(llmLayout),
          autosize: true,
          legend: {
            ...((llmLayout.legend as Record<string, unknown>) || {}),
            orientation: 'h',
            x: 0.5,
            y: 1.08,
            xanchor: 'center',
            yanchor: 'bottom',
            title: { text: '' },  // 隐藏 'variable' 等 px 自动技术标题
          },
        };

        // 3. 准备 config: LLM 的 + 我们强制覆盖 (displaylogo 等)
        const llmConfig = (option.config as Record<string, unknown>) || {};
        const config = { ...llmConfig, ...TOOLBAR_CONFIG };

        await Plotly.newPlot(containerRef.current, data as never, layout, config);

        // 自适应兜底:newPlot 用 chunk 加载完那一瞬间的容器尺寸渲染,
        // 可能在浏览器 layout 稳定之前(字体异步加载/父级 flex 重排/侧边栏
        // 切换等)。等下一帧浏览器 paint 时容器实际尺寸已稳定,触发一次
        // Plots.resize 让 plotly 用真实容器尺寸重新计算 plot area。
        // 跟 ResizeObserver(兜底后续容器变化) + responsive:true(兜底
        // window resize) 互补,覆盖"初次渲染时机"漏洞。
        requestAnimationFrame(() => {
          if (disposed || !containerRef.current) return;
          const Plots = (Plotly as unknown as {
            Plots?: { resize: (el: HTMLElement) => void };
          }).Plots;
          if (Plots?.resize) {
            try { Plots.resize(containerRef.current); } catch { /* ignore */ }
          }
        });

        if (!disposed) {
          setState('ready');
          setErrMsg('');
        }
      } catch (e) {
        if (!disposed) {
          const msg = e instanceof Error ? e.message : String(e);
          logger.error('PlotlyBlock', `init failed | ${msg}`);
          setErrMsg(msg);
          setState('error');
        }
      }
    })();

    return () => {
      disposed = true;
      // 卸载/重渲染:purge 释放 plotly 内部资源
      const el = containerRef.current;
      if (el) {
        getPlotly().then((Plotly) => {
          try { Plotly.purge(el); } catch { /* ignore */ }
        }).catch(() => { /* ignore */ });
      }
    };
  }, [option]);

  // 响应容器尺寸变化(窗口 resize / 侧边栏切换等)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let cancelled = false;
    const ro = new ResizeObserver(() => {
      if (cancelled) return;
      getPlotly().then((P) => {
        if (cancelled) return;
        const Plots = (P as unknown as { Plots?: { resize: (el: HTMLElement) => void } }).Plots;
        if (Plots?.resize) {
          try { Plots.resize(el); } catch { /* ignore */ }
        }
      }).catch(() => { /* ignore */ });
    });
    ro.observe(el);
    return () => { cancelled = true; ro.disconnect(); };
  }, []);

  if (state === 'error') return <ErrorBox message={errMsg} />;

  return (
    // 大厂自适应策略 (Databricks/Hex/Streamlit/Jupyter):
    //   宽度 100% 跟随容器 (响应式)
    //   高度固定 CHART_HEIGHT_PX (对话场景标准, 防图表吞噬上下文)
    //   overflow: hidden (兜底, plotly 任何超出都被截, 不覆盖下方文字)
    <div
      className="my-3 relative"
      style={{ height: CHART_HEIGHT_PX, overflow: 'hidden' }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {state === 'loading' && <LoadingOverlay />}
    </div>
  );
}

export default memo(PlotlyBlockInner);

// 导出常量供测试使用
export { PROFESSIONAL_TEMPLATE, TOOLBAR_CONFIG, CHART_HEIGHT_PX };
